import sys
import json
import random
from datetime import datetime, timezone
from uuid import UUID
from fastapi import HTTPException, status
import asyncpg
from app.utils.names import generate_display_name
from app.utils.avatars import generate_avatar_seed
from app.auth import hash_password, verify_password, create_user_token
from app.state import connections

async def register_user(
    email: str,
    password_plain: str,
    quiz_answers: dict[str, str],
    conn: asyncpg.Connection
) -> dict:
    """Signs up a new user, hashes their password, generates names/avatars,

    creates their records in a single database transaction, and issues a JWT.
    """
    # 1. Check if email already registered
    try:
        existing_user = await conn.fetchval(
            "SELECT id FROM users WHERE email = $1",
            email
        )
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered"
            )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error checking email availability for {email}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )

    # 2. Collision-safe display name generation
    display_name = generate_display_name()
    for _ in range(5):
        try:
            collides = await conn.fetchval(
                "SELECT id FROM users WHERE display_name = $1",
                display_name
            )
            if not collides:
                break
        except Exception as e:
            print(f"Error checking display name collision for {display_name}: {e}", file=sys.stderr)
        display_name = generate_display_name()
    else:
        # Retry limit reached, append a 2-digit number
        display_name = f"{display_name}{random.randint(10, 99)}"

    # 3. Avatar seed generation
    avatar_seed = generate_avatar_seed()

    # 4. Insert into database using a transaction
    hashed_pwd = hash_password(password_plain)
    
    try:
        async with conn.transaction():
            user_row = await conn.fetchrow(
                """
                INSERT INTO users (email, display_name, avatar_seed, quiz_answers, status)
                VALUES ($1, $2, $3, $4, 'waiting')
                RETURNING id, display_name, avatar_seed, status
                """,
                email, display_name, avatar_seed, json.dumps(quiz_answers)
            )
            
            if not user_row:
                raise Exception("Failed to insert user row")
                
            user_id = user_row["id"]
            
            await conn.execute(
                """
                INSERT INTO user_credentials (user_id, password_hash)
                VALUES ($1, $2)
                """,
                user_id, hashed_pwd
            )
            
        token = create_user_token(str(user_id))
        
        return {
            "token": token,
            "user": {
                "id": user_id,
                "display_name": user_row["display_name"],
                "avatar_seed": user_row["avatar_seed"],
                "status": user_row["status"]
            }
        }
    except Exception as e:
        print(f"Transaction failed during signup for {email}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not complete registration due to database transaction failure"
        )

async def authenticate_user(
    email: str,
    password_plain: str,
    conn: asyncpg.Connection
) -> dict:
    """Verifies user login credentials, fetches user profile, and issues a JWT."""
    try:
        row = await conn.fetchrow(
            """
            SELECT u.id, u.display_name, u.avatar_seed, u.status, c.password_hash
            FROM users u
            JOIN user_credentials c ON u.id = c.user_id
            WHERE u.email = $1
            """,
            email
        )
        if not row:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
        
        if not verify_password(password_plain, row["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
            
        token = create_user_token(str(row["id"]))
        
        return {
            "token": token,
            "user": {
                "id": row["id"],
                "display_name": row["display_name"],
                "avatar_seed": row["avatar_seed"],
                "status": row["status"]
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error during login for {email}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )

async def get_user_status(
    user_id: UUID,
    conn: asyncpg.Connection
) -> dict:
    """Retrieves current matchmaking status and active room partner information."""
    try:
        user_row = await conn.fetchrow(
            "SELECT status, room_id FROM users WHERE id = $1",
            user_id
        )
        if not user_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
            
        status_val = user_row["status"]
        room_id = user_row["room_id"]
        
        if status_val == "matched" and room_id:
            # Load active room details and other participant
            room_row = await conn.fetchrow(
                "SELECT user_a, user_b, is_active FROM rooms WHERE id = $1",
                room_id
            )
            if room_row and room_row["is_active"]:
                partner_id = room_row["user_b"] if room_row["user_a"] == user_id else room_row["user_a"]
                partner_row = await conn.fetchrow(
                    "SELECT display_name, avatar_seed FROM users WHERE id = $1",
                    partner_id
                )
                if partner_row:
                    return {
                        "status": "matched",
                        "room_id": room_id,
                        "partner_display_name": partner_row["display_name"],
                        "partner_avatar_seed": partner_row["avatar_seed"]
                    }
        
        # Fallback or standard waiting response
        return {
            "status": "waiting",
            "room_id": None,
            "partner_display_name": None,
            "partner_avatar_seed": None
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error loading status for user_id={user_id}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )

async def submit_quiz(
    user_id: UUID,
    quiz_answers: dict[str, str],
    conn: asyncpg.Connection
) -> dict:
    """Updates user quiz answers in the database."""
    try:
        res = await conn.execute(
            """
            UPDATE users
            SET quiz_answers = $1
            WHERE id = $2
            """,
            json.dumps(quiz_answers), user_id
        )
        if res == "UPDATE 0":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile not found"
            )
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error updating quiz for user_id={user_id}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )

async def get_room_messages(
    room_id: UUID,
    user_id: UUID,
    before_id: UUID | None,
    conn: asyncpg.Connection
) -> list[dict]:
    """Loads historical messages in a room, optionally before a specific message ID."""
    # 1. Authorize user has access to room
    try:
        room_row = await conn.fetchrow(
            "SELECT user_a, user_b, is_active FROM rooms WHERE id = $1",
            room_id
        )
        if not room_row or not room_row["is_active"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Active room not found"
            )
            
        if room_row["user_a"] != user_id and room_row["user_b"] != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this room"
            )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error checking room auth for user_id={user_id}, room_id={room_id}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )

    # 2. Fetch history
    try:
        if before_id:
            # Find the sent_at of the 'before' message first
            before_sent_at = await conn.fetchval(
                "SELECT sent_at FROM messages WHERE id = $1 AND room_id = $2",
                before_id, room_id
            )
            if not before_sent_at:
                return []
                
            rows = await conn.fetch(
                """
                SELECT id, sender_id, content, sent_at
                FROM messages
                WHERE room_id = $1 AND sent_at < $2
                ORDER BY sent_at DESC
                LIMIT 50
                """,
                room_id, before_sent_at
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, sender_id, content, sent_at
                FROM messages
                WHERE room_id = $1
                ORDER BY sent_at DESC
                LIMIT 50
                """,
                room_id
            )
            
        # Reverse the list to get chronological (ASC) order
        messages = [dict(row) for row in reversed(rows)]
        return messages
    except Exception as e:
        print(f"Error fetching room messages for room_id={room_id}, before_id={before_id}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )

async def get_admin_users(
    search: str | None,
    page: int,
    limit: int,
    conn: asyncpg.Connection
) -> dict:
    """Returns a list of users filtered by search keyword and paginated."""
    offset = (page - 1) * limit
    
    try:
        where_clause = ""
        params = []
        if search:
            where_clause = "WHERE email ILIKE $1 OR display_name ILIKE $1"
            params.append(f"%{search}%")
            
        count_query = f"SELECT COUNT(id) FROM users {where_clause}"
        total = await conn.fetchval(count_query, *params)
        
        users_query = f"""
            SELECT id, email, display_name, avatar_seed, status, room_id, quiz_answers, created_at
            FROM users
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
        """
        user_params = list(params) + [limit, offset]
        rows = await conn.fetch(users_query, *user_params)
        
        users = []
        for row in rows:
            u = dict(row)
            # Parse JSON safely
            if isinstance(u["quiz_answers"], str):
                u["quiz_answers"] = json.loads(u["quiz_answers"])
            users.append(u)
            
        return {
            "users": users,
            "total": total,
            "page": page
        }
    except Exception as e:
        print(f"Error fetching admin user list: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )

async def match_users(
    user_a_id: UUID,
    user_b_id: UUID,
    conn: asyncpg.Connection
) -> dict:
    """Matches two waiting users into an active room."""
    if user_a_id == user_b_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot match a user with themselves"
        )

    try:
        # Check both users exist and status is waiting
        rows = await conn.fetch(
            """
            SELECT id, status, display_name, avatar_seed
            FROM users
            WHERE id = $1 OR id = $2
            """,
            user_a_id, user_b_id
        )
        
        if len(rows) != 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or both users do not exist"
            )
            
        user_map = {row["id"]: dict(row) for row in rows}
        
        if user_map[user_a_id]["status"] != "waiting" or user_map[user_b_id]["status"] != "waiting":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or both users are already matched"
            )
            
        async with conn.transaction():
            # Create the room
            room_id = await conn.fetchval(
                """
                INSERT INTO rooms (user_a, user_b, created_by_admin, is_active)
                VALUES ($1, $2, true, true)
                RETURNING id
                """,
                user_a_id, user_b_id
            )
            
            # Update both users
            await conn.execute(
                """
                UPDATE users
                SET status = 'matched', room_id = $1
                WHERE id = $2 OR id = $3
                """,
                room_id, user_a_id, user_b_id
            )
            
        # Notify connected users via Websockets
        await notify_match(room_id, user_a_id, user_map[user_b_id], user_b_id, user_map[user_a_id])
        
        return {"room_id": room_id}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Transaction failed matching user_a={user_a_id} and user_b={user_b_id}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Transaction failed matching users"
        )

async def notify_match(
    room_id: UUID,
    user_a_id: UUID,
    user_b_profile: dict,
    user_b_id: UUID,
    user_a_profile: dict
) -> None:
    """Helper: Dispatches real-time WebSocket notifications to newly matched users."""
    import json
    
    # Look up in connections registry
    # Format of message matching the spec:
    # { "type": "matched", "room_id": "<uuid>", "partner_display_name": "...", "partner_avatar_seed": "..." }
    
    # We must scan rooms in connections?
    # Wait, when a user is waiting, they might not be connected to a room WebSocket yet,
    # or they might be connected to a waiting room socket, or standard status polling is running.
    # The spec mentions: "Notify both users via WebSocket if they have active connections (look up in the in-memory connection registry)."
    # Wait, the connections registry maps: `connections: dict[str, dict[str, WebSocket]]` where key is `room_id` or similar.
    # Wait, let's see. If the user is waiting, which room id is their connection registered under?
    # In WS /ws/{room_id}, the client connects to a room. When they are waiting, they don't have a room yet!
    # Do they connect to a general waiting room socket or `/ws/waiting`?
    # Wait, the spec only lists: `WS /ws/{room_id}`
    # Where does it say they connect?
    # Wait, if they have active connections in `connections`, maybe they connected to `/ws/waiting` or a special key?
    # Wait, "connections: dict[room_id, dict[user_id, WebSocket]]"
    # If they are matched, they don't have a room connection *until* they get matched and connect to `WS /ws/{room_id}`!
    # Ah! But what if they have connections in the registry under some other key (like "waiting" room id or some dummy room ID)?
    # We can check if their `user_id` is registered under *any* room or special dummy room, and send the notification there if online!
    # Let's search all rooms in the `connections` dictionary for `user_a_id` and `user_b_id` and dispatch.
    
    msg_a = {
        "type": "matched",
        "room_id": str(room_id),
        "partner_display_name": user_b_profile["display_name"],
        "partner_avatar_seed": user_b_profile["avatar_seed"]
    }
    
    msg_b = {
        "type": "matched",
        "room_id": str(room_id),
        "partner_display_name": user_a_profile["display_name"],
        "partner_avatar_seed": user_a_profile["avatar_seed"]
    }
    
    for r_id, user_sockets in list(connections.items()):
        if str(user_a_id) in user_sockets:
            try:
                await user_sockets[str(user_a_id)].send_text(json.dumps(msg_a))
            except Exception as e:
                print(f"Error sending match WS notification to user_a={user_a_id}: {e}", file=sys.stderr)
        if str(user_b_id) in user_sockets:
            try:
                await user_sockets[str(user_b_id)].send_text(json.dumps(msg_b))
            except Exception as e:
                print(f"Error sending match WS notification to user_b={user_b_id}: {e}", file=sys.stderr)

async def get_admin_rooms(
    conn: asyncpg.Connection
) -> dict:
    """Returns all active rooms along with participant profiles."""
    try:
        # Strict: NEVER SELECT * - explicitly name joining columns
        rows = await conn.fetch(
            """
            SELECT r.id, r.created_at,
                   ua.id as ua_id, ua.display_name as ua_display_name, ua.avatar_seed as ua_avatar_seed,
                   ub.id as ub_id, ub.display_name as ub_display_name, ub.avatar_seed as ub_avatar_seed
            FROM rooms r
            JOIN users ua ON r.user_a = ua.id
            JOIN users ub ON r.user_b = ub.id
            WHERE r.is_active = true
            ORDER BY r.created_at DESC
            """
        )
        
        rooms = []
        for row in rows:
            rooms.append({
                "id": row["id"],
                "created_at": row["created_at"],
                "user_a": {
                    "id": row["ua_id"],
                    "display_name": row["ua_display_name"],
                    "avatar_seed": row["ua_avatar_seed"]
                },
                "user_b": {
                    "id": row["ub_id"],
                    "display_name": row["ub_display_name"],
                    "avatar_seed": row["ub_avatar_seed"]
                }
            })
            
        return {"rooms": rooms}
    except Exception as e:
        print(f"Error loading active rooms for admin: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )

async def deactivate_room(
    room_id: UUID,
    conn: asyncpg.Connection
) -> dict:
    """Closes an active room and resets user statuses back to waiting."""
    try:
        room_row = await conn.fetchrow(
            "SELECT user_a, user_b, is_active FROM rooms WHERE id = $1",
            room_id
        )
        if not room_row or not room_row["is_active"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Active room not found"
            )
            
        user_a = room_row["user_a"]
        user_b = room_row["user_b"]
        
        async with conn.transaction():
            # Deactivate room
            await conn.execute(
                "UPDATE rooms SET is_active = false WHERE id = $1",
                room_id
            )
            # Reset users status
            await conn.execute(
                """
                UPDATE users
                SET room_id = NULL, status = 'waiting'
                WHERE id = $1 OR id = $2
                """,
                user_a, user_b
            )
            
        # Clean up WebSocket registry and gracefully disconnect
        # We notify the users over the socket if connected, then close them.
        room_key = str(room_id)
        if room_key in connections:
            user_sockets = connections[room_key]
            for user_id_str, ws in list(user_sockets.items()):
                try:
                    # Notify and close
                    await ws.send_text(json.dumps({"type": "deactivated"}))
                    await ws.close(code=4003, reason="Room deactivated by admin")
                except Exception as e:
                    print(f"Error closing WebSocket on deactivation for user={user_id_str}: {e}", file=sys.stderr)
            connections.pop(room_key, None)
            
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Transaction failed during deactivation of room {room_id}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database transaction error during deactivation"
        )
