"""Emails sent over SMTP using only the standard library: sign-up codes and match notifications.

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


def _card_html(content: str, footer: str) -> str:
    """Wraps an email's content in the app's look: a white, ink-bordered card on cream, with small print below it."""
    return f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#FFF5EC;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#FFF5EC;padding:32px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#FFFFFF;border:3px solid #1A1423;border-radius:20px;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#1A1423;">
            <tr>
              <td style="padding:32px 28px;text-align:left;">
{content}
              </td>
            </tr>
          </table>
          <p style="max-width:480px;margin:16px auto 0;font-family:Arial,sans-serif;font-size:12px;line-height:1.5;color:#5C5466;">{footer}</p>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def _new_email(config: SmtpConfig, to_address: str, subject: str, text: str, html: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = to_address
    message["Date"] = formatdate(usegmt=True)
    # Passing the sender's domain avoids make_msgid's hostname lookup, which can stall
    message["Message-ID"] = make_msgid(domain=parseaddr(config.sender)[1].rpartition("@")[2] or None)
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message


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
    content = f"""\
                <p style="margin:0 0 8px;font-size:12px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#D81B55;">Loom &middot; It's a match</p>
                <h1 style="margin:0 0 20px;font-size:32px;line-height:1.1;font-weight:800;">Stop crying.<br>You've got a match.</h1>
                <p style="margin:0 0 16px;font-size:16px;line-height:1.5;">Hi {name},</p>
                <p style="margin:0 0 16px;font-size:16px;line-height:1.5;">I did it. I found someone who might actually be able to handle you.</p>
                <p style="margin:0 0 24px;font-size:16px;line-height:1.5;">Your match goes by <strong style="background:#FFD23F;padding:0 4px;">{partner}</strong>. That's all you get for now. It's blind dating, remember?</p>
                <a href="{url}" style="display:inline-block;padding:14px 24px;background:#FF3D71;color:#1A1423;border:2px solid #1A1423;border-radius:12px;font-size:16px;font-weight:700;text-decoration:none;">Go say hi &rarr;</a>
                <p style="margin:24px 0 0;font-size:16px;line-height:1.5;">Don't leave them on seen.<br>The Matchmaker</p>"""
    html = _card_html(content, "You're getting this because you signed up for Loom and I just matched you with someone.")
    return _new_email(config, to_address, MATCH_EMAIL_SUBJECT, text, html)


def build_otp_email(config: SmtpConfig, to_address: str, otp_code: str, expires_in_minutes: int) -> EmailMessage:
    """Builds the sign-up code email. The code also goes in the subject so it shows in inbox previews."""
    text = (
        f"Your Loom code is {otp_code}\n\n"
        "Type it into the sign-up page and I'll get to work on your love life.\n"
        f"It expires in {expires_in_minutes} minutes, so no dawdling.\n\n"
        "The Matchmaker\n\n"
        "Didn't try to sign up for Loom? Someone typed your email by mistake. Ignore this and nothing happens.\n"
    )

    code = escape(otp_code)
    content = f"""\
                <p style="margin:0 0 8px;font-size:12px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#D81B55;">Loom &middot; Sign-up code</p>
                <h1 style="margin:0 0 20px;font-size:32px;line-height:1.1;font-weight:800;">Prove you're real.</h1>
                <p style="margin:0 0 16px;font-size:16px;line-height:1.5;">Type this into the sign-up page and I'll get to work on your love life.</p>
                <p style="margin:0 0 24px;"><span style="display:inline-block;padding:12px 20px;background:#FFD23F;border:2px solid #1A1423;border-radius:12px;font-family:'Courier New',Courier,monospace;font-size:32px;font-weight:700;letter-spacing:8px;">{code}</span></p>
                <p style="margin:0;font-size:16px;line-height:1.5;">It expires in {expires_in_minutes} minutes, so no dawdling.<br>The Matchmaker</p>"""
    html = _card_html(content, "Didn't try to sign up for Loom? Someone typed your email by mistake. Ignore this and nothing happens.")
    return _new_email(config, to_address, f"{otp_code} is your Loom code", text, html)


def _deliver(config: SmtpConfig, messages: dict[str, EmailMessage]) -> int:
    """Sends every message over one SMTP connection and returns how many were accepted.

    messages is keyed by a label for the logs, like "Match email to user <id>", so addresses stay out of them.
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
        for label, message in messages.items():
            try:
                server.send_message(message)
                sent += 1
            except (smtplib.SMTPException, OSError) as e:
                print(f"{label} was not delivered: {e}", file=sys.stderr)
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
            f"Match email to user {user_a['id']}": build_match_email(config, user_a["email"], user_a["display_name"], user_b["display_name"]),
            f"Match email to user {user_b['id']}": build_match_email(config, user_b["email"], user_b["display_name"], user_a["display_name"]),
        }
        # smtplib blocks, so run it off the event loop that also serves the chat WebSockets
        sent = await asyncio.to_thread(_deliver, config, messages)
    except Exception as e:
        print(f"Sending match emails for users {user_a['id']} and {user_b['id']} failed: {e}", file=sys.stderr)
        return "failed"

    if sent == len(messages):
        return "sent"
    return "partial" if sent else "failed"


async def send_otp_email(to_address: str, otp_code: str, expires_in_minutes: int) -> bool:
    """Emails a sign-up code and never raises. False means it wasn't delivered, or SMTP isn't set up."""
    config = get_smtp_config()
    if config is None:
        return False

    try:
        message = build_otp_email(config, to_address, otp_code, expires_in_minutes)
        sent = await asyncio.to_thread(_deliver, config, {"Sign-up code email": message})
    except Exception as e:
        print(f"Sending a sign-up code email failed: {e}", file=sys.stderr)
        return False
    return sent == 1
