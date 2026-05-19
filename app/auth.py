import os
import sys
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.database import get_db_connection
import asyncpg

# Password hashing configuration
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 schemes
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", scheme_name="UserSecurity")
admin_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/admin/login", scheme_name="AdminSecurity")

def hash_password(password: str) -> str:
    """Hashes a plain password using bcrypt."""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against its bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)

# JWT generation and validation helper functions

def create_user_token(user_id: str) -> str:
    """Generates a custom User JWT token (HS256) valid for 30 days."""
    secret = os.getenv("JWT_SECRET")
    if not secret:
        print("JWT_SECRET is missing from environment variables", file=sys.stderr)
        raise ValueError("JWT_SECRET is not set")
    
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=30)
    payload = {
        "sub": user_id,
        "role": "user",
        "exp": int(expire.timestamp())
    }
    return jwt.encode(payload, secret, algorithm="HS256")

def verify_user_token(token: str) -> str:
    """Verifies a User JWT token and returns the user UUID as a string."""
    secret = os.getenv("JWT_SECRET")
    if not secret:
        print("JWT_SECRET is missing from environment variables", file=sys.stderr)
        raise ValueError("JWT_SECRET is not set")
    
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        role = payload.get("role")
        user_id = payload.get("sub")
        if role != "user" or not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token claims"
            )
        return user_id
    except JWTError as e:
        print(f"User JWT verification failed: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials"
        )

def create_admin_token() -> str:
    """Generates a custom Admin JWT token (HS256) valid for 8 hours."""
    secret = os.getenv("ADMIN_JWT_SECRET")
    if not secret:
        print("ADMIN_JWT_SECRET is missing from environment variables", file=sys.stderr)
        raise ValueError("ADMIN_JWT_SECRET is not set")
    
    now = datetime.now(timezone.utc)
    expire = now + timedelta(hours=8)
    payload = {
        "sub": "admin",
        "role": "admin",
        "exp": int(expire.timestamp())
    }
    return jwt.encode(payload, secret, algorithm="HS256")

def verify_admin_token(token: str) -> str:
    """Verifies an Admin JWT token."""
    secret = os.getenv("ADMIN_JWT_SECRET")
    if not secret:
        print("ADMIN_JWT_SECRET is missing from environment variables", file=sys.stderr)
        raise ValueError("ADMIN_JWT_SECRET is not set")
    
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        role = payload.get("role")
        sub = payload.get("sub")
        if role != "admin" or sub != "admin":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid admin token claims"
            )
        return sub
    except JWTError as e:
        print(f"Admin JWT verification failed: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials"
        )

# FastAPI Route dependencies

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    conn: asyncpg.Connection = Depends(get_db_connection)
) -> dict:
    """FastAPI Dependency: Authenticates a standard user and returns their database row."""
    user_id = verify_user_token(token)
    
    try:
        # Strict: NEVER SELECT * - always name columns explicitly
        row = await conn.fetchrow(
            """
            SELECT id, email, display_name, avatar_seed, quiz_answers, status, room_id, created_at
            FROM users
            WHERE id = $1
            """,
            user_id
        )
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching user profile in dependency (user_id={user_id}): {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )

async def get_current_admin(
    token: str = Depends(admin_oauth2_scheme)
) -> str:
    """FastAPI Dependency: Authenticates the admin."""
    return verify_admin_token(token)
