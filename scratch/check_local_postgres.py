import socket

def check_local_postgres():
    print("Checking if local PostgreSQL is listening on 127.0.0.1:5432...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect(("127.0.0.1", 5432))
        print("[SUCCESS] Something is listening on 127.0.0.1:5432! Local Postgres is available.")
        s.close()
        return True
    except Exception as e:
        print(f"[FAIL] Local Postgres not available on 127.0.0.1:5432: {e}")
        s.close()
        return False

if __name__ == "__main__":
    check_local_postgres()
