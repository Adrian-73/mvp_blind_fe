import os
import sys
import json
from uuid import UUID
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, Depends, HTTPException, status, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.database import init_db, close_db, get_db_connection
from app.auth import get_current_user, get_current_admin, verify_user_token
from app.schemas import (
    SignupRequest, LoginRequest, AuthResponse,
    StatusResponse, MessagesListResponse, MessageResponse,
    AdminLoginRequest, AdminTokenResponse, AdminUsersListResponse,
    MatchRequest, MatchResponse, AdminRoomsListResponse
)
from app.services import (
    register_user, authenticate_user, get_user_status, submit_quiz,
    get_room_messages, get_admin_users, match_users, get_admin_rooms, deactivate_room
)
from app.state import connections
import asyncpg

# Lifespan manager for DB connection pool
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()

app = FastAPI(
    title="Private Blind-Dating Chat Room API",
    description="Backend API for managing private anonymous chat rooms, matchmaking, and real-time WebSockets.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Configuration
frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_url, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Unauthenticated Routes ---

@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    """Unauthenticated health endpoint used by Render and cron-job.org."""
    return {"status": "ok"}

# --- User Auth Endpoints ---

@app.post("/api/signup", response_model=AuthResponse, status_code=status.HTTP_200_OK)
async def signup(
    body: SignupRequest,
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """Signs up a new user and returns their profile with custom JWT."""
    return await register_user(body.email, body.password, body.quiz_answers, conn)

@app.post("/api/auth/login", response_model=AuthResponse, status_code=status.HTTP_200_OK)
async def login(
    body: LoginRequest,
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """Authenticates user credentials and returns user profile with JWT."""
    return await authenticate_user(body.email, body.password, conn)

# --- User Routes ---

@app.get("/api/me/status", response_model=StatusResponse, status_code=status.HTTP_200_OK)
async def get_my_status(
    current_user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """Polled by the client waiting room to verify current matchmaking status."""
    return await get_user_status(current_user["id"], conn)

@app.post("/api/user/quiz", status_code=status.HTTP_200_OK)
async def submit_user_quiz(
    body: dict[str, str],
    current_user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """Allows authenticated users to submit or update their quiz answers."""
    return await submit_quiz(current_user["id"], body, conn)

@app.get("/api/rooms/{room_id}/messages", response_model=MessagesListResponse, status_code=status.HTTP_200_OK)
async def get_messages(
    room_id: UUID,
    before: UUID | None = Query(None, description="Load messages before this message UUID for pagination"),
    current_user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """Fetches up to 50 historical messages for a matched room in ascending order."""
    messages = await get_room_messages(room_id, current_user["id"], before, conn)
    return {"messages": messages}

# --- Admin Auth Endpoints ---

@app.post("/api/admin/login", response_model=AdminTokenResponse, status_code=status.HTTP_200_OK)
async def admin_login(body: AdminLoginRequest):
    """Authenticates the admin using environment variables and generates an Admin JWT."""
    admin_user = os.getenv("ADMIN_USERNAME")
    admin_pwd_hash = os.getenv("ADMIN_PASSWORD_HASH")
    
    if not admin_user or not admin_pwd_hash:
        print("Admin credentials are not configured in environment variables", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin environment setup incomplete"
        )
        
    from app.auth import verify_password, create_admin_token
    
    if body.username != admin_user or not verify_password(body.password, admin_pwd_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials"
        )
        
    token = create_admin_token()
    return {"token": token}

# --- Admin Operations Endpoints ---

@app.get("/api/admin/users", response_model=AdminUsersListResponse, status_code=status.HTTP_200_OK)
async def get_all_users(
    search: str | None = Query(None, description="Search by email or display name"),
    page: int = Query(1, ge=1, description="Page index"),
    limit: int = Query(50, ge=1, le=100, description="Page size limit"),
    current_admin: str = Depends(get_current_admin),
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """Lists all users registered in the system (admin only)."""
    return await get_admin_users(search, page, limit, conn)

@app.post("/api/admin/match", response_model=MatchResponse, status_code=status.HTTP_200_OK)
async def match_waiting_users(
    body: MatchRequest,
    current_admin: str = Depends(get_current_admin),
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """Matches two waiting users into an active chat room (admin only)."""
    return await match_users(body.user_a_id, body.user_b_id, conn)

@app.get("/api/admin/rooms", response_model=AdminRoomsListResponse, status_code=status.HTTP_200_OK)
async def get_active_rooms(
    current_admin: str = Depends(get_current_admin),
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """Lists all active chat rooms (admin only)."""
    return await get_admin_rooms(conn)

@app.post("/api/admin/rooms/{room_id}/deactivate", status_code=status.HTTP_200_OK)
async def deactivate_chat_room(
    room_id: UUID,
    current_admin: str = Depends(get_current_admin),
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """Closes an active chat room and resets the status of both users back to waiting (admin only)."""
    return await deactivate_room(room_id, conn)

# --- Admin CSV Export Endpoints (Streamed Response) ---

async def export_users_csv_stream():
    """Helper: Streams users from DB as CSV chunks without loading all rows in memory."""
    from app.database import _pool
    if not _pool:
        raise RuntimeError("Database pool not initialized")
        
    async with _pool.acquire() as conn:
        import io
        import csv
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header
        writer.writerow(["id", "email", "display_name", "status", "created_at", "q1", "q2", "q3", "q4", "q5"])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        
        # Cursor needs transaction
        async with conn.transaction():
            async for row in conn.cursor(
                """
                SELECT id, email, display_name, status, created_at, quiz_answers
                FROM users
                ORDER BY created_at DESC
                """
            ):
                quiz = {}
                quiz_data = row["quiz_answers"]
                if quiz_data:
                    if isinstance(quiz_data, str):
                        try:
                            quiz = json.loads(quiz_data)
                        except Exception:
                            quiz = {}
                    elif isinstance(quiz_data, dict):
                        quiz = quiz_data
                
                writer.writerow([
                    str(row["id"]),
                    row["email"],
                    row["display_name"],
                    row["status"],
                    row["created_at"].isoformat() if row["created_at"] else "",
                    quiz.get("q1", ""),
                    quiz.get("q2", ""),
                    quiz.get("q3", ""),
                    quiz.get("q4", ""),
                    quiz.get("q5", "")
                ])
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

@app.get("/api/admin/users/export")
async def export_users_csv(current_admin: str = Depends(get_current_admin)):
    """Downloads database users record in CSV format using StreamingResponse (admin only)."""
    return StreamingResponse(
        export_users_csv_stream(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=\"users.csv\""
        }
    )

async def export_messages_csv_stream(room_id: UUID | None):
    """Helper: Streams messaging transcripts from DB as CSV chunks without loading all rows in memory."""
    from app.database import _pool
    if not _pool:
        raise RuntimeError("Database pool not initialized")
        
    async with _pool.acquire() as conn:
        import io
        import csv
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header
        writer.writerow(["id", "room_id", "sender_id", "sender_display_name", "content", "sent_at"])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        
        # Build query
        if room_id:
            query = """
                SELECT m.id, m.room_id, m.sender_id, u.display_name, m.content, m.sent_at
                FROM messages m
                JOIN users u ON m.sender_id = u.id
                WHERE m.room_id = $1
                ORDER BY m.sent_at ASC
            """
            params = [room_id]
        else:
            query = """
                SELECT m.id, m.room_id, m.sender_id, u.display_name, m.content, m.sent_at
                FROM messages m
                JOIN users u ON m.sender_id = u.id
                ORDER BY m.sent_at ASC
            """
            params = []
            
        async with conn.transaction():
            async for row in conn.cursor(query, *params):
                writer.writerow([
                    str(row["id"]),
                    str(row["room_id"]),
                    str(row["sender_id"]),
                    row["display_name"],
                    row["content"],
                    row["sent_at"].isoformat() if row["sent_at"] else ""
                ])
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

@app.get("/api/admin/messages/export")
async def export_messages_csv(
    room_id: UUID | None = Query(None, description="Filter export by room UUID"),
    current_admin: str = Depends(get_current_admin)
):
    """Downloads messaging logs in CSV format using StreamingResponse (admin only)."""
    filename = f"messages_{room_id}.csv" if room_id else "messages.csv"
    return StreamingResponse(
        export_messages_csv_stream(room_id),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\""
        }
    )

# --- WebSocket Room Server ---

@app.websocket("/ws/{room_id}")
async def websocket_room_handler(websocket: WebSocket, room_id: UUID):
    """Establishes real-time duplex chat session inside active matchmaking rooms."""
    token = websocket.query_params.get("token")
    if not token:
        # Invalid handshake: token missing
        await websocket.close(code=4001, reason="Token query param missing")
        return
        
    try:
        user_id_str = verify_user_token(token)
        user_id = UUID(user_id_str)
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return
        
    # Verify room is active and user is a participant
    from app.database import _pool
    if not _pool:
        await websocket.close(code=1011, reason="Database pool uninitialized")
        return
        
    async with _pool.acquire() as conn:
        try:
            room_row = await conn.fetchrow(
                "SELECT user_a, user_b, is_active FROM rooms WHERE id = $1",
                room_id
            )
        except Exception as e:
            print(f"Error querying room {room_id} in WS: {e}", file=sys.stderr)
            await websocket.close(code=1011, reason="Database read error")
            return
            
    if not room_row or not room_row["is_active"]:
        await websocket.close(code=4003, reason="Active room not found")
        return
        
    if room_row["user_a"] != user_id and room_row["user_b"] != user_id:
        await websocket.close(code=4003, reason="Access to room forbidden")
        return

    # Add connection to registry
    room_key = str(room_id)
    user_key = str(user_id)
    
    # Accept handshake
    await websocket.accept()
    connections[room_key][user_key] = websocket
    
    try:
        while True:
            # Await messaging loop
            text_data = await websocket.receive_text()
            
            try:
                data = json.loads(text_data)
                content = data.get("content", "")
            except Exception:
                # Invalid payload shape
                continue
                
            if not isinstance(content, str) or not content.strip():
                # Invalid content length/type
                continue
                
            if len(content) > 2000:
                # Truncate or reject
                continue
                
            # Log message to database
            async with _pool.acquire() as conn:
                try:
                    msg_row = await conn.fetchrow(
                        """
                        INSERT INTO messages (room_id, sender_id, content)
                        VALUES ($1, $2, $3)
                        RETURNING id, sent_at
                        """,
                        room_id, user_id, content
                    )
                except Exception as e:
                    print(f"Error storing message in room={room_id}, sender={user_id}: {e}", file=sys.stderr)
                    continue
                    
            if not msg_row:
                continue
                
            # Broadcast message to matched room participants
            payload = {
                "type": "message",
                "id": str(msg_row["id"]),
                "sender_id": user_key,
                "content": content,
                "sent_at": msg_row["sent_at"].isoformat()
            }
            
            payload_str = json.dumps(payload)
            
            # Send to both users in the room connection list if active
            user_sockets = connections.get(room_key, {})
            for uid_str, ws in list(user_sockets.items()):
                try:
                    await ws.send_text(payload_str)
                except Exception as e:
                    print(f"Failed broadcasting message to user_id={uid_str} in room={room_id}: {e}", file=sys.stderr)
                    
    except WebSocketDisconnect:
        pass
    finally:
        # Gracefully dequeue socket on closure
        try:
            connections[room_key].pop(user_key, None)
            if not connections[room_key]:
                connections.pop(room_key, None)
        except Exception:
            pass
