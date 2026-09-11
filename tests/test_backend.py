import asyncio
import os
import smtplib
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

# Dynamic import: append project root directory to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

# Setup environment variables for test execution
os.environ["SUPABASE_URL"] = "https://mockproject.supabase.co"
os.environ["SUPABASE_KEY"] = "mockservicekey123"
os.environ["JWT_SECRET"] = "test_user_jwt_secret_key_minimum_32_characters"
os.environ["ADMIN_JWT_SECRET"] = "test_admin_jwt_secret_key_minimum_32_characters"
os.environ["ADMIN_USERNAME"] = "test_admin"
os.environ["FRONTEND_URL"] = "http://localhost:5173"

from app.auth import hash_password, create_user_token, create_admin_token

# Pre-generate hashed password dynamically to prevent hash mismatch
os.environ["ADMIN_PASSWORD_HASH"] = hash_password("test_password")

from fastapi.testclient import TestClient
from app.main import app as fastapi_app
from app.mailer import send_match_emails
from app.services import match_users

class MockAPIResponse:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count

class ChainedMock:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.current_table = None

    def table(self, name):
        self.current_table = name
        return self

    def __getattr__(self, name):
        if name == "table":
            return self.table
        return self

    def __call__(self, *args, **kwargs):
        return self

    def execute(self):
        return self.responses.get(self.current_table, MockAPIResponse([]))

class TestChatRoomBackend(unittest.TestCase):
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
        
    def test_health_check(self):
        """Verify the unauthenticated health check endpoint."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @patch("app.main.register_user")
    def test_user_signup_success(self, mock_register):
        """Verify standard user signup router handles Pydantic request and response shapes."""
        mock_user_id = uuid4()
        mock_register.return_value = {
            "token": "mocked_jwt_token",
            "user": {
                "id": str(mock_user_id),
                "display_name": "TestDolphin",
                "avatar_seed": "abc123hex",
                "status": "waiting"
            }
        }
        
        payload = {
            "email": "user@example.com",
            "password": "securepassword123",
            "gender": "female",
            "interested_in": ["male", "non_binary"],
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
        self.assertIn("token", data)
        self.assertEqual(data["user"]["display_name"], "TestDolphin")
        self.assertEqual(data["user"]["status"], "waiting")
        mock_register.assert_called_once()
        # Profile answers reach the service as one dict, with free-text whitespace trimmed
        self.assertEqual(mock_register.call_args.args[3], {
            "gender": "female",
            "interested_in": ["male", "non_binary"],
            "state": "Karnataka",
            "bio": "I love long walks, filter coffee and terrible puns.",
            "single_reason": "Moved cities for work and haven't met the right person yet."
        })

    @patch("app.main.register_user")
    def test_user_signup_rejects_invalid_profile(self, mock_register):
        """Verify signup rejects unknown options, empty attraction choices, and text outside the length limits."""
        valid_payload = {
            "email": "user@example.com",
            "password": "securepassword123",
            "gender": "male",
            "interested_in": ["female"],
            "state": "Non-Indian",
            "bio": "Software engineer who spends weekends hiking.",
            "single_reason": "Too busy climbing mountains."
        }
        invalid_overrides = [
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

    @patch("app.main.authenticate_user")
    def test_user_login_success(self, mock_auth):
        """Verify standard user login router handles Pydantic request and response shapes."""
        mock_user_id = uuid4()
        mock_auth.return_value = {
            "token": "mocked_jwt_token",
            "user": {
                "id": str(mock_user_id),
                "display_name": "TestDolphin",
                "avatar_seed": "abc123hex",
                "status": "waiting"
            }
        }
        
        payload = {
            "email": "user@example.com",
            "password": "securepassword123"
        }
        
        response = self.client.post("/api/auth/login", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["token"], "mocked_jwt_token")
        self.assertEqual(data["user"]["display_name"], "TestDolphin")
        mock_auth.assert_called_once()

    def test_admin_login_success(self):
        """Verify admin authentication returns valid JWT when using credentials matching environment variables."""
        payload = {
            "username": "test_admin",
            "password": "test_password"
        }
        response = self.client.post("/api/admin/login", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("token", data)

    def test_admin_login_failure(self):
        """Verify admin login fails with incorrect password."""
        payload = {
            "username": "test_admin",
            "password": "wrongpassword"
        }
        response = self.client.post("/api/admin/login", json=payload)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid admin credentials")

    @patch("app.main.get_user_status")
    def test_get_my_status_authenticated(self, mock_get_status):
        """Verify user status endpoint requires valid JWT and calls service."""
        mock_user_id = str(uuid4())
        mock_get_status.return_value = {
            "status": "waiting",
            "room_id": None,
            "partner_display_name": None,
            "partner_avatar_seed": None
        }
        
        token = create_user_token(mock_user_id)
        headers = {"Authorization": f"Bearer {token}"}
        
        # Mock database user fetch in Dependency Injection get_current_user
        mock_user_row = {
            "id": mock_user_id,
            "email": "user@example.com",
            "display_name": "TestDolphin",
            "avatar_seed": "abc123hex",
            "quiz_answers": "{}",
            "status": "waiting",
            "room_id": None,
            "created_at": None
        }
        
        self.mock_db_responses["users"] = MockAPIResponse([mock_user_row])
        
        response = self.client.get("/api/me/status", headers=headers)
            
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "waiting")

    @patch("app.main.get_admin_users")
    def test_admin_get_users_unauthorized(self, mock_get_users):
        """Verify admin endpoint rejects standard user tokens."""
        token = create_user_token(str(uuid4()))
        headers = {"Authorization": f"Bearer {token}"}
        
        response = self.client.get("/api/admin/users", headers=headers)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Could not validate credentials")
        mock_get_users.assert_not_called()

    @patch("app.main.get_admin_users")
    def test_admin_get_users_authorized(self, mock_get_users):
        """Verify admin endpoint allows valid admin tokens."""
        mock_get_users.return_value = {
            "users": [],
            "total": 0,
            "page": 1
        }
        token = create_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        response = self.client.get("/api/admin/users", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 0)
        mock_get_users.assert_called_once()

    @patch("app.main.match_users")
    def test_admin_match_passes_email_option(self, mock_match):
        """Verify the match endpoint forwards the admin's email choice and reports how the emails went."""
        room_id = str(uuid4())
        mock_match.return_value = {"room_id": room_id, "email_status": "sent"}
        headers = {"Authorization": f"Bearer {create_admin_token()}"}
        ids = {"user_a_id": str(uuid4()), "user_b_id": str(uuid4())}

        response = self.client.post("/api/admin/match", headers=headers, json={**ids, "notify_by_email": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"room_id": room_id, "email_status": "sent"})
        self.assertTrue(mock_match.call_args.kwargs["notify_by_email"])

        # Clients that don't send the option keep the old behaviour: no emails
        mock_match.return_value = {"room_id": room_id}
        response = self.client.post("/api/admin/match", headers=headers, json=ids)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email_status"], "skipped")
        self.assertFalse(mock_match.call_args.kwargs["notify_by_email"])

    def test_admin_email_config(self):
        """Verify the email config endpoint is admin-only and reflects whether SMTP is set up."""
        headers = {"Authorization": f"Bearer {create_admin_token()}"}
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com", "SMTP_FROM": "Loom <matchmaker@example.com>"}):
            response = self.client.get("/api/admin/email-config", headers=headers)
            self.assertEqual(response.json(), {"enabled": True})
        with patch.dict(os.environ, {"SMTP_HOST": "", "SMTP_FROM": ""}):
            response = self.client.get("/api/admin/email-config", headers=headers)
            self.assertEqual(response.json(), {"enabled": False})

        user_headers = {"Authorization": f"Bearer {create_user_token(str(uuid4()))}"}
        self.assertEqual(self.client.get("/api/admin/email-config", headers=user_headers).status_code, 401)

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
