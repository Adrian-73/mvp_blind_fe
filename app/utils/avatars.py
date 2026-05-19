import secrets

def generate_avatar_seed() -> str:
    """Generates a random 16-character hex string to serve as an avatar seed."""
    return secrets.token_hex(8)
