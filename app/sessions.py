"""Server-side login sessions for users and the admin.

The browser only ever holds a random session token in an httpOnly cookie, out of reach of page
JavaScript. The sessions table stores the token's SHA-256 hash, so a leaked table can't be replayed,
and deleting a row ends that session immediately (logout, log out everywhere, admin password change).
"""
import hashlib
import os
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from supabase import Client

from app.state import connections


@dataclass(frozen=True)
class SessionPolicy:
    role: str
    cookie_name: str
    # A session ends after this long without a request...
    idle_timeout: timedelta
    # ...and after this long however active it is
    max_lifetime: timedelta
    # Requests only slide the expiry forward once last_seen_at is this stale, so status polling doesn't write on every call
    touch_interval: timedelta


USER_SESSION = SessionPolicy(
    role="user",
    cookie_name="loom_session",
    idle_timeout=timedelta(days=30),
    max_lifetime=timedelta(days=90),
    touch_interval=timedelta(minutes=5),
)

ADMIN_SESSION = SessionPolicy(
    role="admin",
    cookie_name="loom_admin_session",
    idle_timeout=timedelta(hours=1),
    max_lifetime=timedelta(hours=8),
    touch_interval=timedelta(minutes=1),
)

# The users row handed to routes as the current user
USER_COLUMNS = "id, email, display_name, avatar_seed, quiz_answers, status, room_id, created_at"

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def hash_token(token: str) -> str:
    """Tokens carry 256 random bits, so a fast hash is enough to make stored rows useless to an attacker."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def admin_fingerprint() -> str | None:
    """Changes whenever ADMIN_USERNAME or ADMIN_PASSWORD_HASH does, which ends every existing admin session."""
    username = os.getenv("ADMIN_USERNAME")
    password_hash = os.getenv("ADMIN_PASSWORD_HASH")
    if not username or not password_hash:
        return None
    return hashlib.sha256(f"{username}\0{password_hash}".encode("utf-8")).hexdigest()


# --- Cookies ---

def _is_https(request: Request) -> bool:
    # Render and Vercel terminate TLS at their proxy and pass the original scheme along
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return proto.split(",")[0].strip().lower() == "https"


def _set_session_cookie(request: Request, response: Response, policy: SessionPolicy, token: str) -> None:
    response.set_cookie(
        policy.cookie_name,
        token,
        # The server enforces the idle timeout; the browser just has to keep the cookie for the longest a session can live
        max_age=int(policy.max_lifetime.total_seconds()),
        path="/",
        httponly=True,
        secure=_is_https(request),
        samesite="lax",
    )


def clear_session_cookie(request: Request, response: Response, policy: SessionPolicy) -> None:
    response.delete_cookie(policy.cookie_name, path="/", httponly=True, secure=_is_https(request), samesite="lax")


# --- Session lifecycle ---

def start_session(db: Client, policy: SessionPolicy, request: Request, response: Response, user_id: str | None = None) -> None:
    """Creates a session and sets its cookie on the response.

    A session this browser already had for the role is ended first, so every login issues a
    brand-new token (no session fixation) and doesn't leave the old one alive.
    """
    now = _now()
    token = secrets.token_urlsafe(32)
    row = {
        "role": policy.role,
        "token_hash": hash_token(token),
        "created_at": now.isoformat(),
        "last_seen_at": now.isoformat(),
        "expires_at": (now + min(policy.idle_timeout, policy.max_lifetime)).isoformat(),
    }
    if policy.role == "user":
        row["user_id"] = str(user_id)
    else:
        row["admin_fingerprint"] = admin_fingerprint()

    try:
        previous_token = request.cookies.get(policy.cookie_name)
        if previous_token:
            _delete_session_by_token(db, policy, previous_token)
        db.table("sessions").insert(row).execute()
    except Exception as e:
        print(f"Starting a {policy.role} session failed: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not start a session"
        )

    _delete_expired_sessions(db, policy, user_id, now)
    _set_session_cookie(request, response, policy, token)


def find_session(db: Client, policy: SessionPolicy, token: str | None) -> dict | None:
    """Returns the live session behind a cookie token, or None.

    User sessions come back with their users row embedded under "users". A live session's expiry
    slides forward, at most once per touch_interval. Database errors propagate to the caller.
    """
    if not token:
        return None

    columns = "id, user_id, admin_fingerprint, created_at, last_seen_at, expires_at"
    if policy.role == "user":
        columns += f", users({USER_COLUMNS})"
    res = db.table("sessions").select(columns).eq("token_hash", hash_token(token)).eq("role", policy.role).execute()
    if not res.data:
        return None

    session = res.data[0]
    now = _now()
    if policy.role == "admin":
        expected_fingerprint = admin_fingerprint()
        is_live = expected_fingerprint is not None and session.get("admin_fingerprint") == expected_fingerprint
    else:
        is_live = bool(session.get("users"))
    if not is_live or _parse_timestamp(session["expires_at"]) <= now:
        _delete_session(db, session["id"])
        return None

    if now - _parse_timestamp(session["last_seen_at"]) >= policy.touch_interval:
        _touch_session(db, policy, session, now)
    return session


def _touch_session(db: Client, policy: SessionPolicy, session: dict, now: datetime) -> None:
    created_at = _parse_timestamp(session["created_at"])
    expires_at = min(now + policy.idle_timeout, created_at + policy.max_lifetime)
    try:
        db.table("sessions").update({
            "last_seen_at": now.isoformat(),
            "expires_at": expires_at.isoformat()
        }).eq("id", session["id"]).execute()
    except Exception as e:
        # Harmless: the session is still valid, and a later request slides the expiry instead
        print(f"Extending session {session['id']} failed: {e}", file=sys.stderr)


async def end_session(db: Client, policy: SessionPolicy, request: Request, response: Response) -> None:
    """Logs this browser out of the role: deletes its session, drops its chat sockets and clears the cookie.

    Safe to call without a session. If the row can't be deleted the cookie is kept, so the user
    isn't told they're logged out while the session still works.
    """
    token = request.cookies.get(policy.cookie_name)
    if token:
        try:
            ended_ids = _delete_session_by_token(db, policy, token)
        except Exception as e:
            print(f"Ending a {policy.role} session failed: {e}", file=sys.stderr)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not log out"
            )
        await close_session_sockets(ended_ids)
    clear_session_cookie(request, response, policy)


async def end_all_user_sessions(db: Client, user_id: str, request: Request, response: Response) -> None:
    """Logs a user out on every device, this one included."""
    try:
        res = db.table("sessions").delete().eq("role", USER_SESSION.role).eq("user_id", str(user_id)).execute()
    except Exception as e:
        print(f"Ending all sessions for user_id={user_id} failed: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not log out"
        )
    await close_session_sockets(row["id"] for row in (res.data or []))
    clear_session_cookie(request, response, USER_SESSION)


def _delete_session_by_token(db: Client, policy: SessionPolicy, token: str) -> list[str]:
    res = db.table("sessions").delete().eq("token_hash", hash_token(token)).eq("role", policy.role).execute()
    return [row["id"] for row in (res.data or [])]


def _delete_session(db: Client, session_id: str) -> None:
    try:
        db.table("sessions").delete().eq("id", session_id).execute()
    except Exception as e:
        print(f"Deleting dead session {session_id} failed: {e}", file=sys.stderr)


def _delete_expired_sessions(db: Client, policy: SessionPolicy, user_id: str | None, now: datetime) -> None:
    """Cleans up this owner's expired rows at login, so the table never needs a scheduled job."""
    try:
        query = db.table("sessions").delete().eq("role", policy.role).lte("expires_at", now.isoformat())
        if policy.role == "user":
            query = query.eq("user_id", str(user_id))
        query.execute()
    except Exception as e:
        print(f"Cleaning up expired {policy.role} sessions failed: {e}", file=sys.stderr)


async def close_session_sockets(session_ids) -> None:
    """Disconnects chat sockets opened by sessions that just ended. Code 4001 sends the browser back to log in."""
    ended = {str(session_id) for session_id in session_ids}
    if not ended:
        return
    for room_sockets in list(connections.values()):
        for user_key, ws in list(room_sockets.items()):
            if getattr(ws.state, "session_id", None) in ended:
                try:
                    await ws.close(code=4001, reason="Logged out")
                except Exception as e:
                    print(f"Error closing WebSocket for ended session of user={user_key}: {e}", file=sys.stderr)


# --- Cross-site request protection ---

def allowed_origins() -> list[str]:
    """Browser origins allowed to call the API with credentials: the configured frontend and the Vite dev server."""
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
    return list(dict.fromkeys([frontend_url, "http://localhost:5173"]))


def is_trusted_origin(headers: Headers) -> bool:
    """CSRF check for requests that carry session cookies.

    Browsers send Origin on cross-origin requests and on POSTs, so a request passes when Origin
    is absent (non-browser clients), is an allowed frontend, or is this server itself.
    """
    origin = headers.get("origin")
    if not origin:
        return True
    if origin.rstrip("/") in allowed_origins():
        return True
    return urlsplit(origin).netloc == headers.get("host")


class TrustedOriginMiddleware:
    """Rejects state-changing requests sent from other sites. SameSite=Lax cookies are the first line; this is the second."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS and not is_trusted_origin(Headers(scope=scope)):
            response = JSONResponse({"detail": "Cross-site request blocked"}, status_code=status.HTTP_403_FORBIDDEN)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
