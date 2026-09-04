import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from flask import Flask


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.auth import flows, user_store
from modules.auth.api import GENERIC_RESET_MESSAGE, bp


class RegistrationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.user_patch = patch.object(
            user_store, "USER_STORE_PATH", os.path.join(self.temp_dir.name, "users.json")
        )
        self.flow_patch = patch.object(
            flows, "FLOW_STORE_PATH", os.path.join(self.temp_dir.name, "flows.json")
        )
        self.user_patch.start()
        self.flow_patch.start()

        self.app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
        self.app.secret_key = "test"
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def tearDown(self):
        self.flow_patch.stop()
        self.user_patch.stop()
        self.temp_dir.cleanup()

    @patch("modules.auth.api.requests.post")
    @patch("modules.auth.flows.secrets.randbelow", return_value=123456)
    def test_registration_verifies_email_and_creates_guest(self, _, mail_post):
        mail_post.return_value = Mock(status_code=200)
        response = self.client.post("/api/auth/register/start", json={
            "username": "new-user",
            "email": "Person@Example.com",
            "password": "strong-pass",
            "password_confirm": "strong-pass",
        })
        self.assertEqual(response.status_code, 200)
        flow_id = response.get_json()["flow_id"]
        sent_payload = mail_post.call_args.kwargs["json"]
        self.assertIn("123456", sent_payload["text"])
        self.assertIn("123456", sent_payload["html"])

        with open(flows.FLOW_STORE_PATH, encoding="utf-8") as file:
            stored_flow = file.read()
        self.assertNotIn("123456", stored_flow)
        self.assertNotIn("strong-pass", stored_flow)

        verified = self.client.post("/api/auth/register/verify", json={
            "flow_id": flow_id,
            "code": "123456",
        })
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.get_json()["role"], "guest")
        self.assertEqual(user_store.get_user_role("new-user"), "guest")
        self.assertEqual(user_store.get_username_by_email("person@example.com"), "new-user")
        with self.client.session_transaction() as session:
            self.assertEqual(session["user"], "new-user")

    @patch("modules.auth.api.requests.post")
    def test_mail_failure_does_not_leave_pending_registration(self, mail_post):
        mail_post.side_effect = requests.ConnectionError("mail down")
        response = self.client.post("/api/auth/register/start", json={
            "username": "new-user",
            "email": "person@example.com",
            "password": "strong-pass",
            "password_confirm": "strong-pass",
        })
        self.assertEqual(response.status_code, 502)
        data = flows._load_unlocked()
        self.assertEqual(data["registrations"], {})

    def test_registration_validates_password_confirmation(self):
        response = self.client.post("/api/auth/register/start", json={
            "username": "new-user",
            "email": "person@example.com",
            "password": "strong-pass",
            "password_confirm": "different-pass",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("不一致", response.get_json()["error"])

    @patch("modules.auth.flows.secrets.randbelow", return_value=123456)
    def test_verification_code_expires_and_limits_attempts(self, _):
        flow_id, _, error = flows.create_registration(
            "new-user", "person@example.com", "password-hash", now=100
        )
        self.assertIsNone(error)
        for _ in range(flows.MAX_CODE_ATTEMPTS - 1):
            self.assertEqual(flows.verify_registration_code(flow_id, "000000", now=101)[1], "invalid-code")
        self.assertEqual(
            flows.verify_registration_code(flow_id, "000000", now=101)[1],
            "too-many-attempts",
        )
        self.assertEqual(
            flows.verify_registration_code(flow_id, "123456", now=101)[1],
            "invalid-or-expired",
        )

        second_flow, _, _ = flows.create_registration(
            "other-user", "other@example.com", "password-hash", client_id="other", now=200
        )
        self.assertEqual(
            flows.verify_registration_code(
                second_flow, "123456", now=200 + flows.REGISTRATION_TTL_SECONDS
            )[1],
            "invalid-or-expired",
        )

    @patch("modules.auth.api.requests.post")
    def test_password_reset_email_contains_username_and_one_time_link(self, mail_post):
        mail_post.return_value = Mock(status_code=200)
        user_store.create_user("recover-me", "old-password", "guest", email="person@example.com")

        response = self.client.post(
            "/api/auth/password/forgot",
            json={"email": "person@example.com"},
            headers={"X-Forwarded-Proto": "https", "Host": "example.test"},
        )
        self.assertEqual(response.status_code, 200)
        sent_payload = mail_post.call_args.kwargs["json"]
        self.assertIn("recover-me", sent_payload["text"])
        token = re.search(r"/reset-password\?token=([^\s]+)", sent_payload["text"]).group(1)

        reset_page = self.client.get(f"/reset-password?token={token}")
        self.assertEqual(reset_page.status_code, 200)
        self.assertIn("recover-me", reset_page.get_data(as_text=True))

        reset = self.client.post("/api/auth/password/reset", json={
            "token": token,
            "password": "new-password",
            "password_confirm": "new-password",
        })
        self.assertEqual(reset.status_code, 200)
        self.assertFalse(user_store.verify_user_password("recover-me", "old-password"))
        self.assertTrue(user_store.verify_user_password("recover-me", "new-password"))
        replay = self.client.post("/api/auth/password/reset", json={
            "token": token,
            "password": "another-password",
            "password_confirm": "another-password",
        })
        self.assertEqual(replay.status_code, 400)

    @patch("modules.auth.api.requests.post")
    def test_unknown_reset_email_uses_generic_response_without_sending(self, mail_post):
        response = self.client.post("/api/auth/password/forgot", json={"email": "nobody@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["message"], GENERIC_RESET_MESSAGE)
        mail_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
