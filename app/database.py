import os
import sys
from typing import Generator
from supabase import create_client, Client

_client: Client | None = None

async def init_db() -> None:
    """Initializes the Supabase HTTP Client using project URL and Service Role Key.
    
    By connecting via standard HTTPS (IPv4/IPv6 dual-stack), we bypass Winsock
    and WSL TCP loopback routing limitations entirely.
    """
    global _client
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("\n" + "="*80, file=sys.stderr)
        print("CRITICAL CONFIGURATION ERROR: SUPABASE CREDENTIALS MISSING!", file=sys.stderr)
        print("Please configure both SUPABASE_URL and SUPABASE_KEY (service_role key)", file=sys.stderr)
        print("inside your local .env file. See implementation_plan.md for details.", file=sys.stderr)
        print("="*80 + "\n", file=sys.stderr)
        raise ValueError("SUPABASE_URL and SUPABASE_KEY environment variables are required")
    
    try:
        # Create Supabase client using standard HTTP connection
        _client = create_client(url, key)
        print("Successfully established Supabase HTTP Client mapping!", file=sys.stderr)
    except Exception as e:
        print(f"Failed to initialize Supabase Client mapping: {e}", file=sys.stderr)
        raise e

async def close_db() -> None:
    """No-op cleanup for the Supabase HTTP Client."""
    pass

def get_db_client() -> Client:
    """Retrieves the active, initialized Supabase Client."""
    global _client
    if not _client:
        raise RuntimeError("Supabase Client has not been initialized. Call init_db() first.")
    return _client

def get_db_connection() -> Generator[Client, None, None]:
    """Dependency that yields the active Supabase Client.
    
    Retains the exact name 'get_db_connection' to maintain compatibility with 
    FastAPI dependency injection layers without causing compilation issues.
    """
    global _client
    if not _client:
        raise RuntimeError("Supabase Client has not been initialized.")
    yield _client
