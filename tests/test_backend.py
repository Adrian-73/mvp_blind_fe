import os
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

if __name__ == "__main__":
    unittest.main()
