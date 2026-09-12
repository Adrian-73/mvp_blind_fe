import asyncio
import os
import smtplib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

# Dynamic import: append project root directory to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

# Setup environment variables for test execution
os.environ["SUPABASE_URL"] = "https://mockproject.supabase.co"
os.environ["SUPABASE_KEY"] = "mockservicekey123"
os.environ["ADMIN_USERNAME"] = "test_admin"
os.environ["FRONTEND_URL"] = "http://localhost:5173"

from app.auth import hash_password

# Pre-generate hashed password dynamically to prevent hash mismatch
os.environ["ADMIN_PASSWORD_HASH"] = hash_password("test_password")

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from app.main import app as fastapi_app
from app.mailer import send_match_emails
from app.schemas import age_from_date_of_birth
from app.services import match_users
from app.sessions import ADMIN_SESSION, USER_SESSION, admin_fingerprint, find_session, hash_token
from app.state import connections

class MockAPIResponse:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count

class ChainedMock:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.current_table = None
        # Every query run, as (table, [(method, args), ...]), e.g. ("sessions", [("delete", ()), ("eq", ("id", "s1"))])
        self.queries = []
        self._method = None

    def table(self, name):
        self.current_table = name
        self.queries.append((name, []))
        return self

    def __getattr__(self, name):
        if name == "table":
            return self.table
        self._method = name
        return self

    def __call__(self, *args, **kwargs):
        if self.queries:
            self.queries[-1][1].append((self._method, args))
        return self

    def execute(self):
        return self.responses.get(self.current_table, MockAPIResponse([]))

    def ran(self, table, *steps):
        """True when some query on the table included every given (method, args) step."""
        return any(name == table and all(step in ops for step in steps) for name, ops in self.queries)

    def written(self, table, method):
        """The payloads passed to insert/update calls on the table."""
        return [args[0] for name, ops in self.queries if name == table for op, args in ops if op == method]

USER_ROW = {
    "id": str(uuid4()),
    "email": "user@example.com",
    "display_name": "TestDolphin",
    "avatar_seed": "abc123hex",
    "quiz_answers": {},
    "status": "waiting",
    "room_id": None,
    "created_at": "2026-09-01T10:00:00+00:00"
}
PUBLIC_USER = {key: USER_ROW[key] for key in ("id", "display_name", "avatar_seed", "status")}
LOGIN = {"email": "user@example.com", "password": "securepassword123"}

def session_row(user=None, admin=False, **overrides):
    """A live sessions row as find_session reads it: user sessions embed their users row, admin ones carry the fingerprint."""
    now = datetime.now(timezone.utc)
    row = {
        "id": str(uuid4()),
        "user_id": None if admin else (user or USER_ROW)["id"],
        "admin_fingerprint": admin_fingerprint() if admin else None,
        "created_at": now.isoformat(),
        "last_seen_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat()
    }
    if not admin:
        row["users"] = user or USER_ROW
    row.update(overrides)
    return row

class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.mock_db_responses = {
            "users": MockAPIResponse([]),
            "rooms": MockAPIResponse([], count=0)
        }
        self.mock_db = ChainedMock(self.mock_db_responses)

        # Patch create_client in app.database
        self.patcher_create_client = patch("app.database.create_client", return_value=self.mock_db)
        self.patcher_create_client.start()

        # Also clean dependency overrides to be safe
        fastapi_app.dependency_overrides.clear()

        self.client_context = TestClient(fastapi_app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        fastapi_app.dependency_overrides.clear()
        self.client_context.__exit__(None, None, None)
        self.patcher_create_client.stop()

    def log_in_as_user(self, user=None, **session_overrides):
        row = session_row(user, **session_overrides)
        self.mock_db_responses["sessions"] = MockAPIResponse([row])
        self.client.cookies.set(USER_SESSION.cookie_name, "user-session-token")
        return row

    def log_in_as_admin(self, **session_overrides):
        row = session_row(admin=True, **session_overrides)
        self.mock_db_responses["sessions"] = MockAPIResponse([row])
        self.client.cookies.set(ADMIN_SESSION.cookie_name, "admin-session-token")
        return row

    def set_cookie_header(self, response, cookie_name):
        return next(header for header in response.headers.get_list("set-cookie") if header.startswith(f"{cookie_name}="))

class TestChatRoomBackend(ApiTestCase):
    def test_health_check(self):
        """Verify the unauthenticated health check endpoint."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @patch("app.main.register_user")
    def test_user_signup_success(self, mock_register):
        """Verify signup handles the Pydantic request shape, returns the profile and logs the user in."""
        mock_register.return_value = PUBLIC_USER

        payload = {
            "email": "user@example.com",
            "password": "securepassword123",
            "gender": "female",
            "interested_in": ["male", "non_binary"],
            "date_of_birth": "1998-04-12",
            "state": "Karnataka",
            "bio": "  I love long walks, filter coffee and terrible puns.  ",
            "single_reason": "  Moved cities for work and haven't met the right person yet.  ",
            "quiz_answers": {
                "q1": "spontaneous",
                "q2": "introvert"
            }
        }

        response = self.client.post("/api/signup", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertNotIn("token", data)
        self.assertEqual(data["user"]["display_name"], "TestDolphin")
        self.assertEqual(data["user"]["status"], "waiting")
        self.assertIn(USER_SESSION.cookie_name, response.cookies)
        mock_register.assert_called_once()
        # Profile answers reach the service as one dict, with free-text whitespace trimmed
        self.assertEqual(mock_register.call_args.args[3], {
            "gender": "female",
            "interested_in": ["male", "non_binary"],
            "date_of_birth": "1998-04-12",
            "state": "Karnataka",
            "bio": "I love long walks, filter coffee and terrible puns.",
            "single_reason": "Moved cities for work and haven't met the right person yet."
        })

    @patch("app.main.register_user")
    def test_user_signup_rejects_invalid_profile(self, mock_register):
        """Verify signup rejects unknown options, empty attraction choices, impossible or under-18 birthdays, and text outside the length limits."""
        today = datetime.now(timezone.utc).date()
        valid_payload = {
            "email": "user@example.com",
            "password": "securepassword123",
            "date_of_birth": "1995-06-30",
            "gender": "male",
            "interested_in": ["female"],
            "state": "Non-Indian",
            "bio": "Software engineer who spends weekends hiking.",
            "single_reason": "Too busy climbing mountains."
        }
        invalid_overrides = [
            {"date_of_birth": (today - timedelta(days=365 * 17)).isoformat()},
            {"date_of_birth": (today + timedelta(days=1)).isoformat()},
            {"date_of_birth": "1900-01-01"},
            {"date_of_birth": "30/06/1995"},
            {"gender": "robot"},
            {"interested_in": []},
            {"interested_in": ["robot"]},
            {"interested_in": "female"},
            {"state": "Atlantis"},
            {"state": None},
            {"bio": "   too short    "},
            {"bio": "x" * 501},
            {"single_reason": "   meh   "},
            {"single_reason": "x" * 301},
        ]
        for override in invalid_overrides:
            with self.subTest(override=override):
                response = self.client.post("/api/signup", json={**valid_payload, **override})
                self.assertEqual(response.status_code, 422)
        mock_register.assert_not_called()

    @patch("app.main.get_admin_users")
    def test_admin_users_include_age(self, mock_get_users):
        """Verify the admin user list works out each user's age from their date of birth, and leaves it empty when unknown."""
        mock_get_users.return_value = {
            "users": [{**USER_ROW, "date_of_birth": "2000-01-01"}, {**USER_ROW, "id": str(uuid4())}],
            "total": 2,
            "page": 1
        }
        self.log_in_as_admin()

        users = self.client.get("/api/admin/users").json()["users"]
        self.assertEqual(users[0]["age"], age_from_date_of_birth("2000-01-01"))
        self.assertIsNone(users[1]["age"])

    def test_age_counts_whole_years(self):
        """Verify age only goes up on the birthday itself."""
        today = datetime(2026, 9, 12).date()
        self.assertEqual(age_from_date_of_birth("2000-09-13", today=today), 25)
        self.assertEqual(age_from_date_of_birth("2000-09-12", today=today), 26)
        self.assertIsNone(age_from_date_of_birth(None))

    @patch("app.main.authenticate_user")
    def test_user_login_success(self, mock_auth):
        """Verify login handles the Pydantic request shape, returns the profile and sets the session cookie."""
        mock_auth.return_value = PUBLIC_USER

        response = self.client.post("/api/auth/login", json=LOGIN)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertNotIn("token", data)
        self.assertEqual(data["user"]["display_name"], "TestDolphin")
        self.assertIn(USER_SESSION.cookie_name, response.cookies)
        mock_auth.assert_called_once()

    def test_admin_login_success(self):
        """Verify admin login with the credentials from the environment starts an admin session."""
        payload = {
            "username": "test_admin",
            "password": "test_password"
        }
        response = self.client.post("/api/admin/login", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"username": "test_admin"})
        self.assertIn(ADMIN_SESSION.cookie_name, response.cookies)

    def test_admin_login_failure(self):
        """Verify admin login fails with incorrect password."""
        payload = {
            "username": "test_admin",
            "password": "wrongpassword"
        }
        response = self.client.post("/api/admin/login", json=payload)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid admin credentials")
        self.assertNotIn(ADMIN_SESSION.cookie_name, response.cookies)
        self.assertEqual(self.mock_db.written("sessions", "insert"), [])

    @patch("app.main.get_user_status")
    def test_get_my_status_authenticated(self, mock_get_status):
        """Verify user status endpoint requires a session and calls the service."""
        mock_get_status.return_value = {
            "status": "waiting",
            "room_id": None,
            "partner_display_name": None,
            "partner_avatar_seed": None
        }
        self.log_in_as_user()

        response = self.client.get("/api/me/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "waiting")
        self.assertEqual(mock_get_status.call_args.args[0], USER_ROW["id"])

    @patch("app.main.get_admin_users")
    def test_admin_get_users_unauthorized(self, mock_get_users):
        """Verify admin endpoint rejects user sessions."""
        self.log_in_as_user()

        response = self.client.get("/api/admin/users")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Not logged in")
        mock_get_users.assert_not_called()

    @patch("app.main.get_admin_users")
    def test_admin_get_users_authorized(self, mock_get_users):
        """Verify admin endpoint allows admin sessions."""
        mock_get_users.return_value = {
            "users": [],
            "total": 0,
            "page": 1
        }
        self.log_in_as_admin()

        response = self.client.get("/api/admin/users")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 0)
        mock_get_users.assert_called_once()

    @patch("app.main.match_users")
    def test_admin_match_passes_email_option(self, mock_match):
        """Verify the match endpoint forwards the admin's email choice and reports how the emails went."""
        room_id = str(uuid4())
        mock_match.return_value = {"room_id": room_id, "email_status": "sent"}
        self.log_in_as_admin()
        ids = {"user_a_id": str(uuid4()), "user_b_id": str(uuid4())}

        response = self.client.post("/api/admin/match", json={**ids, "notify_by_email": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"room_id": room_id, "email_status": "sent"})
        self.assertTrue(mock_match.call_args.kwargs["notify_by_email"])

        # Clients that don't send the option keep the old behaviour: no emails
        mock_match.return_value = {"room_id": room_id}
        response = self.client.post("/api/admin/match", json=ids)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email_status"], "skipped")
        self.assertFalse(mock_match.call_args.kwargs["notify_by_email"])

    def test_admin_email_config(self):
        """Verify the email config endpoint is admin-only and reflects whether SMTP is set up."""
        self.log_in_as_admin()
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com", "SMTP_FROM": "Loom <matchmaker@example.com>"}):
            response = self.client.get("/api/admin/email-config")
            self.assertEqual(response.json(), {"enabled": True})
        with patch.dict(os.environ, {"SMTP_HOST": "", "SMTP_FROM": ""}):
            response = self.client.get("/api/admin/email-config")
            self.assertEqual(response.json(), {"enabled": False})

        self.client.cookies.clear()
        self.log_in_as_user()
        self.assertEqual(self.client.get("/api/admin/email-config").status_code, 401)

    def test_public_metrics_success(self):
        """Verify the unauthenticated public metrics endpoint returns the correct fields."""
        self.mock_db_responses["rooms"] = MockAPIResponse([], count=5)
        response = self.client.get("/api/public/metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("db_connected", data)
        self.assertIn("active_rooms_count", data)
        self.assertIn("connected_users_count", data)
        self.assertTrue(data["db_connected"])
        self.assertEqual(data["active_rooms_count"], 5)

class TestSessions(ApiTestCase):
    @patch("app.main.authenticate_user", return_value=PUBLIC_USER)
    def test_login_keeps_the_token_in_an_httponly_cookie(self, mock_auth):
        """Verify the browser gets the token in an httpOnly SameSite cookie and the database only its hash."""
        response = self.client.post("/api/auth/login", json=LOGIN)
        self.assertEqual(response.status_code, 200)

        cookie = self.set_cookie_header(response, USER_SESSION.cookie_name)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertIn("Path=/", cookie)
        # Plain http here; the next test covers https
        self.assertNotIn("Secure", cookie)

        token = response.cookies[USER_SESSION.cookie_name]
        [inserted] = self.mock_db.written("sessions", "insert")
        self.assertEqual(inserted["role"], "user")
        self.assertEqual(inserted["user_id"], USER_ROW["id"])
        self.assertEqual(inserted["token_hash"], hash_token(token))
        self.assertNotIn(token, str(inserted))

    @patch("app.main.authenticate_user", return_value=PUBLIC_USER)
    def test_session_cookie_is_secure_behind_an_https_proxy(self, mock_auth):
        """Verify the cookie is marked Secure when the proxy says the browser connected over https."""
        response = self.client.post("/api/auth/login", json=LOGIN, headers={"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", self.set_cookie_header(response, USER_SESSION.cookie_name))

    @patch("app.main.authenticate_user", return_value=PUBLIC_USER)
    def test_login_replaces_the_browsers_previous_session(self, mock_auth):
        """Verify logging in ends the session the browser already had and issues a new token (no session fixation)."""
        self.client.cookies.set(USER_SESSION.cookie_name, "old-token")
        response = self.client.post("/api/auth/login", json=LOGIN)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.mock_db.ran("sessions", ("delete", ()), ("eq", ("token_hash", hash_token("old-token")))))
        self.assertNotEqual(response.cookies[USER_SESSION.cookie_name], "old-token")

    def test_user_routes_require_a_live_session(self):
        """Verify missing, unknown, expired and orphaned sessions are refused, and a live one is looked up by hash."""
        self.assertEqual(self.client.get("/api/auth/session").status_code, 401)

        self.client.cookies.set(USER_SESSION.cookie_name, "unknown-token")
        self.assertEqual(self.client.get("/api/auth/session").status_code, 401)

        self.log_in_as_user(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        self.assertEqual(self.client.get("/api/auth/session").status_code, 401)

        # The users row is gone (account deleted) but the session row survived
        self.log_in_as_user(users=None)
        self.assertEqual(self.client.get("/api/auth/session").status_code, 401)

        self.log_in_as_user()
        response = self.client.get("/api/auth/session")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"user": PUBLIC_USER})
        self.assertTrue(self.mock_db.ran("sessions", ("eq", ("token_hash", hash_token("user-session-token")))))

    def test_user_and_admin_sessions_do_not_cross(self):
        """Verify a user session can't reach admin routes and an admin session can't act as a user."""
        self.log_in_as_user()
        self.assertEqual(self.client.get("/api/admin/session").status_code, 401)

        self.client.cookies.clear()
        self.log_in_as_admin()
        self.assertEqual(self.client.get("/api/auth/session").status_code, 401)
        self.assertEqual(self.client.get("/api/admin/session").json(), {"username": "test_admin"})

    def test_changing_the_admin_password_ends_admin_sessions(self):
        """Verify admin sessions die as soon as ADMIN_PASSWORD_HASH changes."""
        self.log_in_as_admin()
        self.assertEqual(self.client.get("/api/admin/session").status_code, 200)
        with patch.dict(os.environ, {"ADMIN_PASSWORD_HASH": "$2b$12$a-rotated-password-hash"}):
            self.assertEqual(self.client.get("/api/admin/session").status_code, 401)

    def test_logout_ends_the_session_and_its_chat_socket(self):
        """Verify logout deletes the session, clears the cookie and closes only the sockets that session opened."""
        row = self.log_in_as_user()
        own_socket = SimpleNamespace(state=SimpleNamespace(session_id=row["id"]), close=AsyncMock())
        other_device_socket = SimpleNamespace(state=SimpleNamespace(session_id=str(uuid4())), close=AsyncMock())
        connections["room-1"] = {USER_ROW["id"]: own_socket}
        connections["room-2"] = {USER_ROW["id"]: other_device_socket}
        self.addCleanup(connections.pop, "room-1", None)
        self.addCleanup(connections.pop, "room-2", None)

        response = self.client.post("/api/auth/logout")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.mock_db.ran("sessions", ("delete", ()), ("eq", ("token_hash", hash_token("user-session-token")))))
        self.assertIn("Max-Age=0", self.set_cookie_header(response, USER_SESSION.cookie_name))
        own_socket.close.assert_awaited_once_with(code=4001, reason="Logged out")
        other_device_socket.close.assert_not_awaited()

    def test_logout_without_a_session_still_succeeds(self):
        """Verify logout is safe to call when the browser is already logged out."""
        response = self.client.post("/api/auth/logout")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.mock_db.ran("sessions", ("delete", ())))

    def test_logout_everywhere_ends_every_session_of_the_user(self):
        """Verify logging out everywhere deletes all of the user's sessions, not just this browser's."""
        self.log_in_as_user()
        response = self.client.post("/api/auth/logout-all")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.mock_db.ran("sessions", ("delete", ()), ("eq", ("user_id", USER_ROW["id"]))))
        self.assertIn("Max-Age=0", self.set_cookie_header(response, USER_SESSION.cookie_name))

    def test_admin_logout(self):
        """Verify admin logout deletes the admin session and clears its cookie."""
        self.log_in_as_admin()
        response = self.client.post("/api/admin/logout")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.mock_db.ran(
            "sessions", ("delete", ()), ("eq", ("token_hash", hash_token("admin-session-token"))), ("eq", ("role", "admin"))
        ))
        self.assertIn("Max-Age=0", self.set_cookie_header(response, ADMIN_SESSION.cookie_name))

    @patch("app.main.authenticate_user", return_value=PUBLIC_USER)
    def test_cross_site_posts_are_blocked(self, mock_auth):
        """Verify state-changing requests from other sites are refused while the app's own origins get through."""
        blocked = self.client.post("/api/auth/login", json=LOGIN, headers={"Origin": "https://evil.example"})
        self.assertEqual(blocked.status_code, 403)
        mock_auth.assert_not_called()

        for origin in ("http://localhost:5173", "http://testserver"):
            with self.subTest(origin=origin):
                response = self.client.post("/api/auth/login", json=LOGIN, headers={"Origin": origin})
                self.assertEqual(response.status_code, 200)

        # Reads don't change anything, so they're left to CORS
        self.assertEqual(self.client.get("/health", headers={"Origin": "https://evil.example"}).status_code, 200)

    def websocket_close_code(self, url, **kwargs):
        with self.client.websocket_connect(url, **kwargs) as websocket:
            with self.assertRaises(WebSocketDisconnect) as closed:
                websocket.receive_text()
        return closed.exception.code

    def test_websocket_requires_a_session(self):
        """Verify a chat socket without a session is closed with 4001, which the client can actually see."""
        self.assertEqual(self.websocket_close_code(f"/ws/{uuid4()}"), 4001)

    def test_websocket_refuses_other_sites(self):
        """Verify chat handshakes started by pages on other sites are refused."""
        self.log_in_as_user()
        self.assertEqual(self.websocket_close_code(f"/ws/{uuid4()}", headers={"Origin": "https://evil.example"}), 4003)

    def test_websocket_refuses_rooms_the_user_is_not_in(self):
        """Verify a logged-in user can't join someone else's room."""
        self.log_in_as_user()
        self.mock_db_responses["rooms"] = MockAPIResponse([{"user_a": str(uuid4()), "user_b": str(uuid4()), "is_active": True}])
        self.assertEqual(self.websocket_close_code(f"/ws/{uuid4()}"), 4003)

class TestSessionExpiry(unittest.TestCase):
    def find(self, **overrides):
        db = ChainedMock({"sessions": MockAPIResponse([session_row(**overrides)])})
        return db, find_session(db, USER_SESSION, "session-token")

    def test_recent_activity_does_not_write(self):
        """Verify frequent requests (like status polling) don't update the row every time."""
        db, session = self.find()
        self.assertIsNotNone(session)
        self.assertEqual(db.written("sessions", "update"), [])

    def test_activity_slides_the_expiry_forward(self):
        """Verify a request after touch_interval pushes expiry out to a full idle timeout from now."""
        now = datetime.now(timezone.utc)
        db, session = self.find(last_seen_at=(now - timedelta(minutes=10)).isoformat())
        self.assertIsNotNone(session)
        [update] = db.written("sessions", "update")
        expires_at = datetime.fromisoformat(update["expires_at"])
        self.assertAlmostEqual(expires_at.timestamp(), (now + USER_SESSION.idle_timeout).timestamp(), delta=5)

    def test_expiry_never_passes_the_max_lifetime(self):
        """Verify an always-active session still ends max_lifetime after it started."""
        now = datetime.now(timezone.utc)
        created_at = now - USER_SESSION.max_lifetime + timedelta(hours=1)
        db, session = self.find(created_at=created_at.isoformat(), last_seen_at=(now - timedelta(minutes=10)).isoformat())
        [update] = db.written("sessions", "update")
        self.assertEqual(datetime.fromisoformat(update["expires_at"]), created_at + USER_SESSION.max_lifetime)

    def test_expired_session_is_refused_and_deleted(self):
        """Verify an expired session is rejected and its row cleaned up."""
        db, session = self.find(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        self.assertIsNone(session)
        self.assertTrue(db.ran("sessions", ("delete", ())))

SMTP_ENV = {
    "SMTP_HOST": "smtp.example.com",
    "SMTP_PORT": "2525",
    "SMTP_USERNAME": "loom",
    "SMTP_PASSWORD": "smtp-secret",
    "SMTP_FROM": "Loom <matchmaker@example.com>",
    "SMTP_USE_SSL": "",
    "FRONTEND_URL": "https://loom.example.com/",
}

class TestMatchEmails(unittest.TestCase):
    user_a = {"id": str(uuid4()), "email": "calm.river@example.com", "display_name": "CalmRiver", "avatar_seed": "aaa", "status": "waiting"}
    user_b = {"id": str(uuid4()), "email": "otter@example.com", "display_name": "MysticOtter", "avatar_seed": "bbb", "status": "waiting"}

    def send(self):
        return asyncio.run(send_match_emails(self.user_a, self.user_b))

    def test_not_configured_without_smtp_settings(self):
        """Verify nothing is sent, and the admin is told why, when SMTP isn't set up."""
        with patch.dict(os.environ, {"SMTP_HOST": "", "SMTP_FROM": ""}), patch("app.mailer.smtplib.SMTP") as mock_smtp:
            self.assertEqual(self.send(), "not_configured")
        mock_smtp.assert_not_called()

    @patch.dict(os.environ, SMTP_ENV)
    @patch("app.mailer.smtplib.SMTP")
    def test_emails_each_user_about_their_partner(self, mock_smtp):
        """Verify both users are emailed over one TLS-upgraded, authenticated connection."""
        self.assertEqual(self.send(), "sent")

        mock_smtp.assert_called_once_with("smtp.example.com", 2525, timeout=15)
        server = mock_smtp.return_value
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("loom", "smtp-secret")

        email_a, email_b = [call.args[0] for call in server.send_message.call_args_list]
        self.assertEqual(email_a["To"], "calm.river@example.com")
        self.assertEqual(email_b["To"], "otter@example.com")
        body_a = email_a.get_body(("plain",)).get_content()
        self.assertIn("MysticOtter", body_a)
        self.assertIn("https://loom.example.com/waiting", body_a)
        self.assertIn("CalmRiver", email_b.get_body(("plain",)).get_content())
        # Blind dating: neither email reveals the partner's address
        self.assertNotIn("otter@example.com", email_a.as_string())
        self.assertNotIn("calm.river@example.com", email_b.as_string())

    @patch.dict(os.environ, SMTP_ENV)
    @patch("app.mailer.smtplib.SMTP")
    def test_one_rejected_recipient_is_partial(self, mock_smtp):
        """Verify a single refused address is reported as a partial send."""
        mock_smtp.return_value.send_message.side_effect = [
            None,
            smtplib.SMTPRecipientsRefused({"otter@example.com": (550, b"No such user")}),
        ]
        self.assertEqual(self.send(), "partial")

    @patch.dict(os.environ, SMTP_ENV)
    @patch("app.mailer.smtplib.SMTP", side_effect=OSError("Connection refused"))
    def test_unreachable_server_fails_without_raising(self, mock_smtp):
        """Verify a dead SMTP server is reported as failed instead of raising."""
        self.assertEqual(self.send(), "failed")

    @patch.dict(os.environ, SMTP_ENV)
    @patch("app.mailer.smtplib.SMTP")
    def test_refuses_to_send_password_without_tls(self, mock_smtp):
        """Verify credentials are never sent over a connection that can't be encrypted."""
        mock_smtp.return_value.has_extn.return_value = False
        self.assertEqual(self.send(), "failed")
        mock_smtp.return_value.login.assert_not_called()

    @patch.dict(os.environ, {**SMTP_ENV, "SMTP_PORT": "465"})
    @patch("app.mailer.smtplib.SMTP_SSL")
    def test_port_465_uses_implicit_tls(self, mock_smtp_ssl):
        """Verify port 465 connects with TLS from the start rather than STARTTLS."""
        self.assertEqual(self.send(), "sent")
        mock_smtp_ssl.return_value.starttls.assert_not_called()

    @patch("app.services.send_match_emails", new_callable=AsyncMock, return_value="sent")
    def test_match_only_emails_when_asked(self, mock_send):
        """Verify match_users emails the pair only when the admin opts in, after the match is saved."""
        def match_db():
            return ChainedMock({
                "users": MockAPIResponse([self.user_a, self.user_b]),
                "rooms": MockAPIResponse([{"id": "room-1"}]),
            })
        user_a_id, user_b_id = self.user_a["id"], self.user_b["id"]

        result = asyncio.run(match_users(user_a_id, user_b_id, match_db()))
        self.assertEqual(result, {"room_id": "room-1", "email_status": "skipped"})
        mock_send.assert_not_called()

        result = asyncio.run(match_users(user_a_id, user_b_id, match_db(), notify_by_email=True))
        self.assertEqual(result, {"room_id": "room-1", "email_status": "sent"})
        mock_send.assert_awaited_once_with(self.user_a, self.user_b)

if __name__ == "__main__":
    unittest.main()
