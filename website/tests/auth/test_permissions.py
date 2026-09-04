import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.auth import user_store
from modules.auth.api import bp as auth_bp
from modules.chat.api import _resolve_chat_identity
from modules.cloud.api import bp as cloud_bp
from modules.situation.api import bp as situation_bp


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.user_path = os.path.join(self.temp_dir.name, "users.json")
        self.store_patch = patch.object(user_store, "USER_STORE_PATH", self.user_path)
        self.store_patch.start()

    def tearDown(self):
        self.store_patch.stop()
        self.temp_dir.cleanup()

    def test_guest_and_member_have_distinct_permissions(self):
        self.assertEqual(user_store.create_user("visitor", "pw", "guest"), (True, None))
        self.assertEqual(user_store.create_user("private", "pw", "member"), (True, None))

        self.assertEqual(user_store.get_user_permissions("visitor"), [])
        self.assertTrue(user_store.user_has_permission("private", "situation:view"))
        self.assertTrue(user_store.user_has_permission("private", "cloud:delete"))

    def test_invalid_role_is_rejected(self):
        self.assertEqual(user_store.create_user("wrong", "pw", "owner"), (False, "invalid-role"))

    def test_role_can_be_changed_by_account_management(self):
        user_store.create_user("visitor", "pw", "guest")
        self.assertEqual(user_store.update_user_role("visitor", "member"), (True, None))
        self.assertEqual(user_store.get_user_role("visitor"), "member")

    def test_old_or_unknown_role_does_not_gain_permissions(self):
        with open(self.user_path, "w", encoding="utf-8") as file:
            json.dump({"old": {"password_hash": "x", "role": "user"}}, file)
        self.assertEqual(user_store.get_user_role("old"), "guest")
        self.assertEqual(user_store.get_user_permissions("old"), [])

    def test_auth_status_exposes_permissions(self):
        user_store.create_user("private", "pw", "member")
        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(auth_bp)
        client = app.test_client()

        response = client.post("/api/auth/login", json={"username": "private", "password": "pw"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["role"], "member")
        self.assertEqual(response.get_json()["permissions"], ["cloud:delete", "situation:view"])

    def test_guest_cannot_read_situation_or_delete_cloud(self):
        user_store.create_user("visitor", "pw", "guest")
        app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
        app.secret_key = "test"
        app.register_blueprint(situation_bp)
        app.register_blueprint(cloud_bp)
        client = app.test_client()
        with client.session_transaction() as session:
            session["logged_in"] = True
            session["user"] = "visitor"

        situation_response = client.get("/api/situation/latest")
        cloud_response = client.post("/api/cloud/delete/example.txt")

        self.assertEqual(situation_response.status_code, 403)
        self.assertEqual(cloud_response.status_code, 403)

    def test_member_can_read_situation_and_reach_cloud_delete_handler(self):
        user_store.create_user("private", "pw", "member")
        app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
        app.secret_key = "test"
        app.register_blueprint(situation_bp)
        app.register_blueprint(cloud_bp)
        client = app.test_client()
        with client.session_transaction() as session:
            session["logged_in"] = True
            session["user"] = "private"

        self.assertEqual(client.get("/api/situation/latest").status_code, 200)
        with patch("modules.cloud.api.load_meta", return_value={}), patch(
            "modules.cloud.api.resolve_stored_name", return_value=None
        ):
            self.assertEqual(client.post("/api/cloud/delete/missing.txt").status_code, 404)

    def test_logged_in_chat_identity_is_stable_and_account_bound(self):
        user_store.create_user("visitor", "pw", "guest")
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context("/"):
            from flask import session

            session["logged_in"] = True
            session["user"] = "visitor"
            first, first_name = _resolve_chat_identity("device-one")
            second, second_name = _resolve_chat_identity("device-two")

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("account:"))
        self.assertIsNone(first_name)
        self.assertIsNone(second_name)

    def test_username_change_keeps_chat_identity_and_email(self):
        user_store.create_user("visitor", "old-password", "guest", email="person@example.com")
        original_id = user_store.get_user_id("visitor")

        self.assertEqual(
            user_store.update_username("visitor", "new-name", "old-password"),
            (True, None),
        )
        self.assertFalse(user_store.user_exists("visitor"))
        self.assertTrue(user_store.user_exists("new-name"))
        self.assertEqual(user_store.get_user_id("new-name"), original_id)
        self.assertEqual(user_store.get_user_email("new-name"), "person@example.com")

    def test_account_api_requires_current_password_and_updates_session(self):
        user_store.create_user("visitor", "old-password", "guest", email="person@example.com")
        app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
        app.secret_key = "test"
        app.register_blueprint(auth_bp)
        client = app.test_client()
        with client.session_transaction() as session:
            session["logged_in"] = True
            session["user"] = "visitor"
            session["role"] = "guest"

        denied = client.post("/api/auth/account/username", json={
            "new_username": "new-name",
            "current_password": "wrong-password",
        })
        self.assertEqual(denied.status_code, 400)
        self.assertTrue(user_store.user_exists("visitor"))

        renamed = client.post("/api/auth/account/username", json={
            "new_username": "new-name",
            "current_password": "old-password",
        })
        self.assertEqual(renamed.status_code, 200)
        with client.session_transaction() as session:
            self.assertEqual(session["user"], "new-name")

        changed = client.post("/api/auth/account/password", json={
            "current_password": "old-password",
            "new_password": "new-password",
            "new_password_confirm": "new-password",
        })
        self.assertEqual(changed.status_code, 200)
        self.assertTrue(user_store.verify_user_password("new-name", "new-password"))

    def test_account_page_requires_login(self):
        app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
        app.secret_key = "test"
        app.register_blueprint(auth_bp)
        client = app.test_client()
        self.assertEqual(client.get("/account").status_code, 302)

        user_store.create_user("visitor", "old-password", "guest", email="person@example.com")
        with client.session_transaction() as session:
            session["logged_in"] = True
            session["user"] = "visitor"
        response = client.get("/account")
        self.assertEqual(response.status_code, 200)
        self.assertIn("账户管理", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
