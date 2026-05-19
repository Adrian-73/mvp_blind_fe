import os
import sys
import asyncpg
from typing import AsyncGenerator

_pool: asyncpg.Pool | None = None

async def init_db() -> None:
    """Initializes the asyncpg connection pool."""
    global _pool
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is missing from the environment variables", file=sys.stderr)
        raise ValueError("DATABASE_URL environment variable is not set")
    
    try:
        _pool = await asyncpg.create_pool(
            dsn=database_url,
            min_size=1,
            max_size=10,
            command_timeout=60.0
        )
    except Exception as e:
        print(f"Failed to create asyncpg database pool: {e}", file=sys.stderr)
        raise e

async def close_db() -> None:
    """Closes the asyncpg connection pool."""
    global _pool
    if _pool:
        try:
            await _pool.close()
        except Exception as e:
            print(f"Error closing database pool: {e}", file=sys.stderr)

async def get_db_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    """Dependency that yields a database connection from the pool."""
    global _pool
    if not _pool:
        print("Database connection pool is not initialized", file=sys.stderr)
        raise RuntimeError("Database pool has not been initialized.")
    
    async with _pool.acquire() as connection:
        yield connection
