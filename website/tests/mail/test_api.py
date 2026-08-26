import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.mail.api import bp


class MailApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
        self.app.secret_key = "test"
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def login(self):
        with self.client.session_transaction() as session:
            session["logged_in"] = True
            session["user"] = "admin"

    def test_send_requires_login(self):
        response = self.client.post("/api/mail/send", json={})
        self.assertEqual(response.status_code, 403)

    @patch("modules.mail.api.is_admin_user", return_value=False)
    @patch("modules.mail.api.user_exists", return_value=True)
    @patch("modules.mail.api.requests.request")
    def test_regular_user_gets_masked_account_metadata(self, request_mock, *_):
        self.login()
        upstream = Mock(status_code=200, content=b"{}")
        upstream.json.return_value = {"accounts": {"qq": {
            "id": "qq", "address": "private@example.com", "lastError": "private detail"
        }}, "forwardingRules": [{"recipients": ["target@example.com"]}], "forwardingExecutions": []}
        request_mock.return_value = upstream
        payload = self.client.get("/api/mail/accounts").get_json()
        self.assertEqual(payload["accounts"]["qq"]["address"], "pr***@example.com")
        self.assertNotIn("lastError", payload["accounts"]["qq"])
        self.assertNotIn("forwardingRules", payload)
        self.assertNotIn("forwardingExecutions", payload)

    @patch("modules.mail.api.is_admin_user", return_value=True)
    @patch("modules.mail.api.user_exists", return_value=True)
    @patch("modules.mail.api.requests.request")
    def test_home_send_uses_server_default_and_ignores_requested_account(self, request_mock, *_):
        self.login()
        upstream = Mock(status_code=200, content=b"{}")
        upstream.json.return_value = {"status": "sent", "accountId": "qq"}
        request_mock.return_value = upstream
        response = self.client.post("/api/mail/send", json={
            "accountId": "qq",
            "to": "one@example.com, two@example.com",
            "cc": "copy@example.com",
            "subject": "subject",
            "text": "body",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(request_mock.call_args.args[1].endswith("/api/mail/send/default"))
        forwarded = request_mock.call_args.kwargs["json"]
        self.assertNotIn("accountId", forwarded)
        self.assertEqual(forwarded["to"], ["one@example.com", "two@example.com"])
        self.assertEqual(forwarded["cc"], ["copy@example.com"])
        self.assertNotIn("password", str(response.get_json()).lower())

    @patch("modules.mail.api.is_admin_user", return_value=True)
    @patch("modules.mail.api.user_exists", return_value=True)
    @patch("modules.mail.api.requests.request")
    def test_admin_send_can_select_account(self, request_mock, *_):
        self.login()
        upstream = Mock(status_code=200, content=b"{}")
        upstream.json.return_value = {"status": "sent", "accountId": "lmu"}
        request_mock.return_value = upstream
        response = self.client.post("/1/api/mail/send", json={
            "accountId": "lmu", "to": "one@example.com", "subject": "subject", "text": "body"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request_mock.call_args.kwargs["json"]["accountId"], "lmu")
        self.assertTrue(request_mock.call_args.args[1].endswith("/api/mail/send"))

    @patch("modules.mail.api.is_admin_user", return_value=True)
    @patch("modules.mail.api.user_exists", return_value=True)
    def test_admin_page_requires_admin(self, *_):
        self.login()
        self.assertEqual(self.client.get("/1/mail").status_code, 200)


if __name__ == "__main__":
    unittest.main()
