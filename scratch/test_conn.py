import os
import asyncio
import asyncpg
from dotenv import load_dotenv
from urllib.parse import urlparse, urlunparse

load_dotenv()

async def test_username_port(username, port):
    db_url = os.getenv("DATABASE_URL")
    parsed = urlparse(db_url)
    
    # Override username and port
    netloc = f"{username}:{parsed.password}@{parsed.hostname}:{port}"
    test_url = urlunparse(parsed._replace(netloc=netloc))
    
    print(f"Testing User: {username} on Port: {port}...")
    try:
        conn = await asyncpg.connect(test_url, timeout=10.0)
        print(f"🎉 SUCCESS! Connected as {username} on port {port}!")
        await conn.close()
        return True
    except Exception as e:
        print(f"❌ FAILED for {username} on port {port}: {type(e).__name__}: {e}")
        return False

async def main():
    project_ref = "qmishftefwydxqngmhrf"
    users = [
        f"postgresql.{project_ref}",
        f"postgres.{project_ref}"
    ]
    ports = [5432, 6543]
    
    for user in users:
        for port in ports:
            success = await test_username_port(user, port)
            if success:
                print("\nMatch found! You should use this connection combination.")
            print("-" * 60)

if __name__ == "__main__":
    asyncio.run(main())
