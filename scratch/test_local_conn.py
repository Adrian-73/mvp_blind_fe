import asyncio
import asyncpg

async def test_local():
    local_url = "postgresql://postgres:postgres@127.0.0.1:5432/postgres"
    print(f"Testing connection to local Postgres: {local_url}")
    try:
        conn = await asyncpg.connect(local_url, timeout=5.0)
        print("[SUCCESS] Successfully connected to local PostgreSQL database!")
        await conn.close()
        return True
    except Exception as e:
        print(f"[FAIL] Could not connect to local PostgreSQL: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(test_local())
