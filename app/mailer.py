"""Match notification emails, sent over SMTP using only the standard library.

Any provider with an SMTP relay works (Brevo, Resend, Mailgun, SES, a Gmail app
password...). Email counts as switched off until SMTP_HOST and SMTP_FROM are set.
"""
import asyncio
import os
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from html import escape

SMTP_TIMEOUT_SECONDS = 15
MATCH_EMAIL_SUBJECT = "Stop crying. You've got a match."


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    use_ssl: bool
    username: str | None
    password: str | None
    sender: str


def get_smtp_config() -> SmtpConfig | None:
    """Reads SMTP settings from the environment; None means email is not set up."""
    host = os.getenv("SMTP_HOST")
    sender = os.getenv("SMTP_FROM")
    if not host or not sender:
        return None

    try:
        port = int(os.getenv("SMTP_PORT") or 587)
    except ValueError:
        print(f"SMTP_PORT must be a number, got {os.getenv('SMTP_PORT')!r}; email is disabled", file=sys.stderr)
        return None

    # Port 465 is TLS from the first byte; other ports upgrade the connection with STARTTLS
    default_ssl = "true" if port == 465 else "false"
    use_ssl = (os.getenv("SMTP_USE_SSL") or default_ssl).strip().lower() in ("1", "true", "yes")

    return SmtpConfig(
        host=host,
        port=port,
        use_ssl=use_ssl,
        username=os.getenv("SMTP_USERNAME") or None,
        password=os.getenv("SMTP_PASSWORD") or None,
        sender=sender,
    )


def is_email_configured() -> bool:
    return get_smtp_config() is not None


def build_match_email(config: SmtpConfig, to_address: str, display_name: str, partner_display_name: str) -> EmailMessage:
    """Builds the "you've been matched" email in the app's matchmaker voice.

    Only codenames go in, so the partner's email address and profile stay private.
    """
    # /waiting forwards a logged-in user into their new chat and sends everyone else to log in first
    chat_url = f"{os.getenv('FRONTEND_URL', 'http://localhost:5173').rstrip('/')}/waiting"

    text = (
        f"Hi {display_name},\n\n"
        "I did it. I found someone who might actually be able to handle you.\n\n"
        f"Your match goes by {partner_display_name}. That's all you get for now. It's blind dating, remember?\n\n"
        f"Go say hi: {chat_url}\n\n"
        "Don't leave them on seen.\n"
        "The Matchmaker\n\n"
        "You're getting this because you signed up for Loom and I just matched you with someone.\n"
    )

    name = escape(display_name)
    partner = escape(partner_display_name)
    url = escape(chat_url, quote=True)
    html = f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#FFF5EC;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#FFF5EC;padding:32px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#FFFFFF;border:3px solid #1A1423;border-radius:20px;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#1A1423;">
            <tr>
              <td style="padding:32px 28px;text-align:left;">
                <p style="margin:0 0 8px;font-size:12px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#D81B55;">Loom &middot; It's a match</p>
                <h1 style="margin:0 0 20px;font-size:32px;line-height:1.1;font-weight:800;">Stop crying.<br>You've got a match.</h1>
                <p style="margin:0 0 16px;font-size:16px;line-height:1.5;">Hi {name},</p>
                <p style="margin:0 0 16px;font-size:16px;line-height:1.5;">I did it. I found someone who might actually be able to handle you.</p>
                <p style="margin:0 0 24px;font-size:16px;line-height:1.5;">Your match goes by <strong style="background:#FFD23F;padding:0 4px;">{partner}</strong>. That's all you get for now. It's blind dating, remember?</p>
                <a href="{url}" style="display:inline-block;padding:14px 24px;background:#FF3D71;color:#1A1423;border:2px solid #1A1423;border-radius:12px;font-size:16px;font-weight:700;text-decoration:none;">Go say hi &rarr;</a>
                <p style="margin:24px 0 0;font-size:16px;line-height:1.5;">Don't leave them on seen.<br>The Matchmaker</p>
              </td>
            </tr>
          </table>
          <p style="max-width:480px;margin:16px auto 0;font-family:Arial,sans-serif;font-size:12px;line-height:1.5;color:#5C5466;">You're getting this because you signed up for Loom and I just matched you with someone.</p>
        </td>
      </tr>
    </table>
  </body>
</html>
"""

    message = EmailMessage()
    message["Subject"] = MATCH_EMAIL_SUBJECT
    message["From"] = config.sender
    message["To"] = to_address
    message["Date"] = formatdate(usegmt=True)
    # Passing the sender's domain avoids make_msgid's hostname lookup, which can stall
    message["Message-ID"] = make_msgid(domain=parseaddr(config.sender)[1].rpartition("@")[2] or None)
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message


def _deliver(config: SmtpConfig, messages: dict[str, EmailMessage]) -> int:
    """Sends every message over one SMTP connection and returns how many were accepted.

    messages is keyed by user id, which is what gets logged so addresses stay out of the logs.
    """
    tls = ssl.create_default_context()
    if config.use_ssl:
        server = smtplib.SMTP_SSL(config.host, config.port, timeout=SMTP_TIMEOUT_SECONDS, context=tls)
    else:
        server = smtplib.SMTP(config.host, config.port, timeout=SMTP_TIMEOUT_SECONDS)

    with server:
        if not config.use_ssl:
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls(context=tls)
                server.ehlo()
            elif config.username:
                raise smtplib.SMTPNotSupportedError("Server does not offer STARTTLS; refusing to send the password unencrypted")
        if config.username:
            server.login(config.username, config.password or "")

        sent = 0
        for user_id, message in messages.items():
            try:
                server.send_message(message)
                sent += 1
            except (smtplib.SMTPException, OSError) as e:
                print(f"Match email to user {user_id} was not delivered: {e}", file=sys.stderr)
        return sent


async def send_match_emails(user_a: dict, user_b: dict) -> str:
    """Emails both newly matched users about each other, and never raises.

    Returns "sent", "partial", "failed" or "not_configured". The match is already saved
    by the time this runs, so a delivery problem is reported instead of undoing it.
    """
    config = get_smtp_config()
    if config is None:
        print(f"Match emails requested for users {user_a['id']} and {user_b['id']}, but SMTP is not configured", file=sys.stderr)
        return "not_configured"

    try:
        messages = {
            user_a["id"]: build_match_email(config, user_a["email"], user_a["display_name"], user_b["display_name"]),
            user_b["id"]: build_match_email(config, user_b["email"], user_b["display_name"], user_a["display_name"]),
        }
        # smtplib blocks, so run it off the event loop that also serves the chat WebSockets
        sent = await asyncio.to_thread(_deliver, config, messages)
    except Exception as e:
        print(f"Sending match emails for users {user_a['id']} and {user_b['id']} failed: {e}", file=sys.stderr)
        return "failed"

    if sent == len(messages):
        return "sent"
    return "partial" if sent else "failed"
