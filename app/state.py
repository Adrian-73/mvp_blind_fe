from collections import defaultdict
from fastapi import WebSocket

# { room_id (str) -> { user_id (str) -> WebSocket } }
connections: dict[str, dict[str, WebSocket]] = defaultdict(dict)
