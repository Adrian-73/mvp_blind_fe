import sys
import json
import random
import string
from datetime import datetime, timezone, timedelta
from uuid import UUID
from fastapi import HTTPException, status
from supabase import Client
from app.utils.names import generate_display_name
from app.utils.avatars import generate_avatar_seed
from app.auth import hash_password, verify_password
from app.state import connections
from app.mailer import send_match_emails

async def register_user(
    email: str,
    password_plain: str,
    quiz_answers: dict[str, str],
    profile: dict[str, str | list[str]],
    db: Client
) -> dict:
    """Signs up a new user, hashes their password, generates display names/avatars,
    
    creates records inside Supabase over HTTP, and returns the new user's public profile.
    """
    # 1. Check if email already registered
    try:
        res = db.table("users").select("id").eq("email", email).execute()
        if res.data:
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
            res = db.table("users").select("id").eq("display_name", display_name).execute()
            if not res.data:
                break
        except Exception as e:
            print(f"Error checking display name collision for {display_name}: {e}", file=sys.stderr)
        display_name = generate_display_name()
    else:
        # Retry limit reached, append a 2-digit number
        display_name = f"{display_name}{random.randint(10, 99)}"

    # 3. Avatar seed generation
    avatar_seed = generate_avatar_seed()

    # 4. Insert into database with simulated programmatic rollback
    hashed_pwd = hash_password(password_plain)
    user_id = None
    
    try:
        # Insert user profile row
        res = db.table("users").insert({
            "email": email,
            "display_name": display_name,
            "avatar_seed": avatar_seed,
            "quiz_answers": quiz_answers,
            **profile,
            "status": "waiting"
        }).execute()
        
        if not res.data:
            raise Exception("Failed to insert user profile row")
            
        user_row = res.data[0]
        user_id = user_row["id"]
        
        # Insert user password credentials
        db.table("user_credentials").insert({
            "user_id": user_id,
            "password_hash": hashed_pwd
        }).execute()
        
        return {
            "id": user_id,
            "display_name": user_row["display_name"],
            "avatar_seed": user_row["avatar_seed"],
            "status": user_row["status"]
        }
    except Exception as e:
        print(f"Signup failed for {email}: {e}", file=sys.stderr)
        # Programmatic Rollback: If user profile was inserted but credentials failed, clean up the profile
        if user_id:
            try:
                db.table("users").delete().eq("id", user_id).execute()
            except Exception as rollback_err:
                print(f"Cleanup rollback failed for user profile {user_id}: {rollback_err}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not complete registration due to database transaction failure"
        )

async def authenticate_user(
    email: str,
    password_plain: str,
    db: Client
) -> dict:
    """Verifies user login credentials and returns the user's public profile."""
    try:
        # Fetch profile and password hash using PostgREST relation selection
        res = db.table("users").select(
            "id, display_name, avatar_seed, status, user_credentials(password_hash)"
        ).eq("email", email).execute()
        
        if not res.data:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
        
        row = res.data[0]
        cred_data = row.get("user_credentials")
        
        password_hash = None
        if isinstance(cred_data, dict):
            password_hash = cred_data.get("password_hash")
        elif isinstance(cred_data, list) and cred_data:
            password_hash = cred_data[0].get("password_hash")
            
        if not password_hash or not verify_password(password_plain, password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
            
        return {
            "id": row["id"],
            "display_name": row["display_name"],
            "avatar_seed": row["avatar_seed"],
            "status": row["status"]
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
    db: Client
) -> dict:
    """Retrieves current matchmaking status and active room partner information."""
    try:
        res = db.table("users").select("status, room_id").eq("id", str(user_id)).execute()
        if not res.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
            
        user_row = res.data[0]
        status_val = user_row["status"]
        room_id = user_row["room_id"]
        
        if status_val == "matched" and room_id:
            res_room = db.table("rooms").select("user_a, user_b, is_active").eq("id", room_id).execute()
            if res_room.data:
                room_row = res_room.data[0]
                if room_row["is_active"]:
                    partner_id = room_row["user_b"] if room_row["user_a"] == str(user_id) else room_row["user_a"]
                    res_partner = db.table("users").select("display_name, avatar_seed").eq("id", partner_id).execute()
                    if res_partner.data:
                        partner_row = res_partner.data[0]
                        return {
                            "status": "matched",
                            "room_id": room_id,
                            "partner_display_name": partner_row["display_name"],
                            "partner_avatar_seed": partner_row["avatar_seed"]
                        }
        
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
    db: Client
) -> dict:
    """Updates user quiz answers in the database."""
    try:
        res = db.table("users").update({"quiz_answers": quiz_answers}).eq("id", str(user_id)).execute()
        if not res.data:
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
    db: Client
) -> list[dict]:
    """Loads historical messages in a room, optionally before a specific message ID."""
    # 1. Authorize user has access to room
    try:
        res_room = db.table("rooms").select("user_a, user_b, is_active").eq("id", str(room_id)).execute()
        if not res_room.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Active room not found"
            )
        room_row = res_room.data[0]
        if not room_row["is_active"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Active room not found"
            )
            
        if room_row["user_a"] != str(user_id) and room_row["user_b"] != str(user_id):
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
            res_before = db.table("messages").select("sent_at").eq("id", str(before_id)).eq("room_id", str(room_id)).execute()
            if not res_before.data:
                return []
            before_sent_at = res_before.data[0]["sent_at"]
            
            res_msg = db.table("messages").select("id, sender_id, content, sent_at")\
                .eq("room_id", str(room_id))\
                .lt("sent_at", before_sent_at)\
                .order("sent_at", desc=True)\
                .limit(50).execute()
        else:
            res_msg = db.table("messages").select("id, sender_id, content, sent_at")\
                .eq("room_id", str(room_id))\
                .order("sent_at", desc=True)\
                .limit(50).execute()
            
        # Reverse chronological list to ascending order
        messages = [dict(row) for row in reversed(res_msg.data)]
        return messages
    except Exception as e:
        print(f"Error fetching room messages for room_id={room_id}, before_id={before_id}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error"
        )

def _escape_postgrest_value(value: str) -> str:
    """Escapes a value for safe interpolation into a PostgREST filter string.

    PostgREST treats comma, dot, and parentheses as filter syntax. Wrapping the
    value in double quotes neutralizes them, so any backslashes/quotes already
    present in the value must themselves be escaped first (per PostgREST's
    quoted-value syntax) to prevent breaking out of the quoted string.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

async def get_admin_users(
    search: str | None,
    page: int,
    limit: int,
    db: Client,
    status_filter: str | None = None
) -> dict:
    """Returns a list of users filtered by search keyword and paginated (admin only)."""
    offset = (page - 1) * limit

    try:
        query = db.table("users").select("id, email, display_name, avatar_seed, status, room_id, quiz_answers, date_of_birth, gender, interested_in, state, bio, single_reason, created_at", count="exact")

        if search:
            pattern = _escape_postgrest_value(f"%{search}%")
            query = query.or_(f"email.ilike.{pattern},display_name.ilike.{pattern}")

        if status_filter:
            query = query.eq("status", status_filter)

        res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        
        users = []
        for row in res.data:
            u = dict(row)
            if isinstance(u["quiz_answers"], str):
                try:
                    u["quiz_answers"] = json.loads(u["quiz_answers"])
                except Exception:
                    pass
            users.append(u)
            
        return {
            "users": users,
            "total": res.count if res.count is not None else len(users),
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
    db: Client,
    notify_by_email: bool = False
) -> dict:
    """Matches two waiting users into an active room with simulated transactional safety (admin only).

    With notify_by_email, both users are emailed once the match is saved; the returned
    email_status says how that went ("skipped" when emails weren't requested).
    """
    if user_a_id == user_b_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot match a user with themselves"
        )

    try:
        res_users = db.table("users").select("id, email, status, display_name, avatar_seed").in_("id", [str(user_a_id), str(user_b_id)]).execute()
        
        if len(res_users.data) != 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or both users do not exist"
            )
            
        user_map = {row["id"]: dict(row) for row in res_users.data}
        
        if user_map[str(user_a_id)]["status"] != "waiting" or user_map[str(user_b_id)]["status"] != "waiting":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or both users are already matched"
            )
            
        room_id = None
        matched_ids: list[str] = []
        try:
            # 1. Insert room record
            res_room = db.table("rooms").insert({
                "user_a": str(user_a_id),
                "user_b": str(user_b_id),
                "created_by_admin": True,
                "is_active": True
            }).execute()

            if not res_room.data:
                raise Exception("Failed to insert room row")

            room_id = res_room.data[0]["id"]

            # 2. Update both users status, conditioned on them still being "waiting".
            # This is a compare-and-swap: if a concurrent admin match request already
            # flipped one of these users' status between our check above and now, the
            # eq("status", "waiting") clause means that row is silently excluded from
            # the update instead of being clobbered, so we can detect and roll back.
            res_update = db.table("users").update({
                "status": "matched",
                "room_id": room_id
            }).in_("id", [str(user_a_id), str(user_b_id)]).eq("status", "waiting").execute()

            matched_ids = [row["id"] for row in (res_update.data or [])]
            if len(matched_ids) != 2:
                raise Exception(
                    f"Race condition: expected to match 2 users but only matched {len(matched_ids)} "
                    f"(one or both users were matched concurrently by another request)"
                )

        except Exception as match_err:
            print(f"Match assignment execution failed: {match_err}", file=sys.stderr)
            # Programmatic Rollback: revert any partially-applied user status change,
            # then delete the room, so a failed/raced match leaves no half-applied state.
            if matched_ids:
                try:
                    db.table("users").update({"status": "waiting", "room_id": None}).in_("id", matched_ids).execute()
                except Exception as rollback_err:
                    print(f"Cleanup rollback failed for users {matched_ids}: {rollback_err}", file=sys.stderr)
            if room_id:
                try:
                    db.table("rooms").delete().eq("id", room_id).execute()
                except Exception as rollback_err:
                    print(f"Cleanup rollback failed for room {room_id}: {rollback_err}", file=sys.stderr)
            raise match_err
        # Dispatch WS notifications
        await notify_match(room_id, user_a_id, user_map[str(user_b_id)], user_b_id, user_map[str(user_a_id)])

        # Emails go last: the match is already saved, and send_match_emails never raises
        email_status = "skipped"
        if notify_by_email:
            email_status = await send_match_emails(user_map[str(user_a_id)], user_map[str(user_b_id)])

        return {"room_id": room_id, "email_status": email_status}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Match transaction failed for user_a={user_a_id} and user_b={user_b_id}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed matching users"
        )

async def notify_match(
    room_id: str,
    user_a_id: UUID,
    user_b_profile: dict,
    user_b_id: UUID,
    user_a_profile: dict
) -> None:
    """Helper: Dispatches real-time WebSocket notifications to newly matched users."""
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
    db: Client,
    status_filter: str = "active"
) -> dict:
    """Returns rooms based on the active status filter along with participant profiles."""
    try:
        # Navigate relationships using parenthesized targeting for multiple user FKs
        query = db.table("rooms").select(
            "id, created_at, is_active, ua:users!fk_user_a(id, display_name, avatar_seed), ub:users!fk_user_b(id, display_name, avatar_seed)"
        ).order("created_at", desc=True)
        
        if status_filter == "active":
            query = query.eq("is_active", True)
        elif status_filter == "inactive":
            query = query.eq("is_active", False)
            
        res = query.execute()
        
        rooms = []
        for row in res.data:
            rooms.append({
                "id": row["id"],
                "created_at": row["created_at"],
                "is_active": row["is_active"],
                "user_a": row["ua"],
                "user_b": row["ub"]
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
    db: Client,
    reason: str = "Room deactivated by admin"
) -> dict:
    """Closes an active room and resets user statuses back to waiting."""
    try:
        res_room = db.table("rooms").select("user_a, user_b, is_active").eq("id", str(room_id)).execute()
        if not res_room.data or not res_room.data[0]["is_active"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Active room not found"
            )
            
        room_row = res_room.data[0]
        user_a = room_row["user_a"]
        user_b = room_row["user_b"]
        
        try:
            # 1. Close active status of room
            db.table("rooms").update({"is_active": False}).eq("id", str(room_id)).execute()
            # 2. Reset user associations back to waiting
            db.table("users").update({"room_id": None, "status": "waiting"}).in_("id", [user_a, user_b]).execute()
        except Exception as deact_err:
            print(f"Failed programmatically deactivating room: {deact_err}", file=sys.stderr)
            raise deact_err
            
        # Clean up WebSocket registry and gracefully disconnect
        room_key = str(room_id)
        if room_key in connections:
            user_sockets = connections[room_key]
            for user_id_str, ws in list(user_sockets.items()):
                try:
                    await ws.send_text(json.dumps({"type": "deactivated"}))
                    await ws.close(code=4003, reason=reason)
                except Exception as e:
                    print(f"Error closing WebSocket on deactivation for user={user_id_str}: {e}", file=sys.stderr)
            connections.pop(room_key, None)
            
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error during deactivation of room {room_id}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error during deactivation"
        )

async def send_email_otp(email: str, db: Client) -> dict:
    """Generates a 6-digit OTP, stores it with expiry, and simulates sending an email."""
    # Generate 6-digit code
    otp_code = ''.join(random.choices(string.digits, k=6))

    # 10 minutes expiry
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()

    try:
        # Requesting a fresh OTP resets the failed-attempt counter for a clean slate.
        db.table("email_otps").upsert({
            "email": email,
            "otp_code": otp_code,
            "expires_at": expires_at,
            "attempts": 0
        }).execute()
        
        # Simulate Email Delivery securely to the console
        print(f"\n{'='*50}\n[MOCK EMAIL] To: {email}\nSubject: Your Login Code\n\nYour one-time password is: {otp_code}\nIt expires in 10 minutes.\n{'='*50}\n", file=sys.stderr)
        
        return {"status": "success", "message": "OTP sent successfully"}
    except Exception as e:
        print(f"Error sending OTP for {email}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate OTP"
        )

MAX_OTP_ATTEMPTS = 5

async def verify_email_otp(email: str, otp_code: str, db: Client) -> dict:
    """Verifies the OTP. If valid, acts as login (or auto-signup if user doesn't exist)."""
    try:
        # Check OTP
        res = db.table("email_otps").select("*").eq("email", email).execute()
        if not res.data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OTP")

        record = res.data[0]

        expires_at = datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires_at:
            # Delete expired record
            db.table("email_otps").delete().eq("email", email).execute()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OTP")

        attempts = record.get("attempts") or 0
        if attempts >= MAX_OTP_ATTEMPTS:
            # Too many failed guesses: burn the code so it can't be brute-forced further.
            # The user must request a fresh OTP to try again.
            db.table("email_otps").delete().eq("email", email).execute()
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts. Please request a new code.")

        if record["otp_code"] != otp_code:
            # Record the failed attempt so repeated guesses eventually get locked out.
            db.table("email_otps").update({"attempts": attempts + 1}).eq("email", email).execute()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OTP")

        # OTP is valid, delete it to prevent reuse
        db.table("email_otps").delete().eq("email", email).execute()
        
        # Check if user exists
        user_res = db.table("users").select("*").eq("email", email).execute()
        if user_res.data:
            # User exists, proceed with login
            user_row = user_res.data[0]
            return {
                "id": user_row["id"],
                "display_name": user_row["display_name"],
                "avatar_seed": user_row["avatar_seed"],
                "status": user_row["status"]
            }
        else:
            # Auto-signup
            display_name = generate_display_name()
            for _ in range(5):
                name_res = db.table("users").select("id").eq("display_name", display_name).execute()
                if not name_res.data:
                    break
                display_name = generate_display_name()
            else:
                display_name = f"{display_name}{random.randint(10, 99)}"
                
            avatar_seed = generate_avatar_seed()
            
            insert_res = db.table("users").insert({
                "email": email,
                "display_name": display_name,
                "avatar_seed": avatar_seed,
                "quiz_answers": {},
                "status": "waiting"
            }).execute()
            
            if not insert_res.data:
                raise Exception("Failed to insert user profile row")
                
            new_user = insert_res.data[0]
            return {
                "id": new_user["id"],
                "display_name": new_user["display_name"],
                "avatar_seed": new_user["avatar_seed"],
                "status": new_user["status"]
            }
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error verifying OTP for {email}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed"
        )
