import os
import sys
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyCookie
from app.database import get_db_connection
from app.sessions import ADMIN_SESSION, USER_SESSION, SessionPolicy, find_session
from supabase import Client

# Password hashing configuration
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Session cookies, set by the login routes. Declared as security schemes so /docs marks the routes that need them.
user_session_cookie = APIKeyCookie(name=USER_SESSION.cookie_name, scheme_name="UserSession", auto_error=False)
admin_session_cookie = APIKeyCookie(name=ADMIN_SESSION.cookie_name, scheme_name="AdminSession", auto_error=False)

def hash_password(password: str) -> str:
    """Hashes a plain password using bcrypt."""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against its bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)

def _require_session(db: Client, policy: SessionPolicy, token: str | None) -> dict:
    try:
        session = find_session(db, policy, token)
    except Exception as e:
        print(f"Error loading {policy.role} session: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not logged in"
        )
    return session

# FastAPI Route dependencies

async def get_user_session(
    token: str | None = Depends(user_session_cookie),
    db: Client = Depends(get_db_connection)
) -> dict:
    """FastAPI Dependency: the caller's live user session, with their users row under "users"."""
    return _require_session(db, USER_SESSION, token)

async def get_current_user(session: dict = Depends(get_user_session)) -> dict:
    """FastAPI Dependency: Authenticates a standard user and returns their database row."""
    return session["users"]

async def get_admin_session(
    token: str | None = Depends(admin_session_cookie),
    db: Client = Depends(get_db_connection)
) -> dict:
    """FastAPI Dependency: the caller's live admin session."""
    return _require_session(db, ADMIN_SESSION, token)

async def get_current_admin(session: dict = Depends(get_admin_session)) -> str:
    """FastAPI Dependency: Authenticates the admin and returns their username."""
    return os.getenv("ADMIN_USERNAME", "admin")
