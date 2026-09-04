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
from modules.realtime import socketio
from modules.realtime.socket import _origin_allowed
from modules.rtc.api import bp as rtc_bp
from modules.rtc.ice import ice_config
from modules.rtc.registry import RegistryError, RtcRegistry, registry
from modules.rtc import signaling as rtc_signaling  # noqa: F401


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = RtcRegistry()

    def test_invites_and_join_tokens_are_single_use(self):
        session, creator_join = self.registry.create_session("user:a", "Alice")
        invite, _ = self.registry.create_invite(session["session_id"], "user:a")
        joined, guest_join = self.registry.redeem_invite(invite, "user:b", "Bob")

        self.assertEqual(len(joined["participants"]), 2)
        with self.assertRaisesRegex(RegistryError, "invalid-invite"):
            self.registry.redeem_invite(invite, "user:c", "Carol")

        creator, peers = self.registry.join_socket(creator_join, "user:a", "sid-a")
        self.assertEqual(peers, [])
        guest, peers = self.registry.join_socket(guest_join, "user:b", "sid-b")
        self.assertEqual(peers[0]["participant_id"], creator["participant_id"])
        with self.assertRaisesRegex(RegistryError, "invalid-join-token"):
            self.registry.join_socket(guest_join, "user:b", "sid-b2")

    def test_signaling_cannot_cross_sessions(self):
        first, first_token = self.registry.create_session("user:a", "Alice")
        second, second_token = self.registry.create_session("user:b", "Bob")
        sender, _ = self.registry.join_socket(first_token, "user:a", "sid-a")
        target, _ = self.registry.join_socket(second_token, "user:b", "sid-b")

        with self.assertRaisesRegex(RegistryError, "peer-not-connected"):
            self.registry.signal_target("sid-a", first["session_id"], target["participant_id"])
        with self.assertRaisesRegex(RegistryError, "not-joined"):
            self.registry.signal_target("sid-a", second["session_id"], sender["participant_id"])

    def test_expired_session_invalidates_credentials(self):
        now = [1000]
        timed = RtcRegistry(clock=lambda: now[0])
        session, token = timed.create_session("user:a", "Alice", ttl=300)
        now[0] = session["expires_at"] + 1
        with self.assertRaisesRegex(RegistryError, "invalid-join-token"):
            timed.join_socket(token, "user:a", "sid-a")

    def test_turn_hook_uses_ephemeral_credentials(self):
        with patch.dict(os.environ, {
            "RTC_STUN_URLS": "stun:example.test:3478",
            "RTC_TURN_URLS": "turn:turn.example.test:3478?transport=udp",
            "RTC_TURN_SHARED_SECRET": "test-shared-secret",
            "RTC_TURN_TTL_SECONDS": "600",
        }, clear=False):
            result = ice_config("account-id")
        self.assertTrue(result["turn_available"])
        self.assertEqual(len(result["iceServers"]), 2)
        self.assertNotIn("account-id", result["iceServers"][1]["username"])
        self.assertNotEqual(result["iceServers"][1]["credential"], "test-shared-secret")


class SocketOriginTests(unittest.TestCase):
    def test_accepts_same_host_across_tls_terminating_proxy(self):
        self.assertTrue(_origin_allowed(
            "https://xiaociwei.cc", {"HTTP_HOST": "xiaociwei.cc"}
        ))

    def test_accepts_same_host_and_port_for_local_development(self):
        self.assertTrue(_origin_allowed(
            "http://127.0.0.1:8080", {"HTTP_HOST": "127.0.0.1:8080"}
        ))

    def test_rejects_cross_host_and_opaque_origins(self):
        environ = {"HTTP_HOST": "xiaociwei.cc"}
        self.assertFalse(_origin_allowed("https://attacker.example", environ))
        self.assertFalse(_origin_allowed("null", environ))


class RtcApiAndSocketTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store_patch = patch.object(
            user_store, "USER_STORE_PATH", os.path.join(self.temporary.name, "users.json")
        )
        self.store_patch.start()
        user_store.create_user("alice", "password", "guest")
        user_store.create_user("bobby", "password", "guest")
        user_store.create_user("carol", "password", "guest")
        registry.reset()

        self.app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
        self.app.secret_key = "test-secret"
        self.app.register_blueprint(rtc_bp)
        socketio.init_app(self.app)
        self.alice = self._client("alice")
        self.bobby = self._client("bobby")

    def tearDown(self):
        registry.reset()
        self.store_patch.stop()
        self.temporary.cleanup()

    def _client(self, username):
        client = self.app.test_client()
        with client.session_transaction() as flask_session:
            flask_session["logged_in"] = True
            flask_session["user"] = username
            flask_session["role"] = "guest"
        return client

    def test_authenticated_call_flow_and_targeted_signaling(self):
        created = self.alice.post("/api/rtc/sessions", json={"kind": "call"})
        self.assertEqual(created.status_code, 201)
        created_payload = created.get_json()
        session_id = created_payload["session"]["session_id"]

        invitation = self.alice.post(f"/api/rtc/sessions/{session_id}/invites", json={})
        self.assertEqual(invitation.status_code, 201)
        self.assertIn("#invite=", invitation.get_json()["invite_url"])
        redeemed = self.bobby.post("/api/rtc/invites/redeem", json={
            "invite_token": invitation.get_json()["invite_token"],
        })
        self.assertEqual(redeemed.status_code, 200)

        socket_a = socketio.test_client(self.app, flask_test_client=self.alice, namespace="/rtc")
        socket_b = socketio.test_client(self.app, flask_test_client=self.bobby, namespace="/rtc")
        self.assertTrue(socket_a.is_connected("/rtc"))
        self.assertTrue(socket_b.is_connected("/rtc"))
        joined_a = socket_a.emit(
            "rtc_join", {"join_token": created_payload["join_token"]},
            namespace="/rtc", callback=True,
        )
        joined_b = socket_b.emit(
            "rtc_join", {"join_token": redeemed.get_json()["join_token"]},
            namespace="/rtc", callback=True,
        )
        self.assertTrue(joined_a["ok"])
        self.assertTrue(joined_b["ok"])

        target_id = joined_b["participant"]["participant_id"]
        acknowledged = socket_a.emit("rtc_signal", {
            "session_id": session_id,
            "to": target_id,
            "kind": "description",
            "payload": {"type": "offer", "sdp": "v=0\r\n"},
        }, namespace="/rtc", callback=True)
        self.assertTrue(acknowledged["ok"])
        received = socket_b.get_received("/rtc")
        relayed = [event for event in received if event["name"] == "rtc_signal"]
        self.assertEqual(len(relayed), 1)
        self.assertEqual(relayed[0]["args"][0]["payload"]["sdp"], "v=0\r\n")

        socket_a.disconnect(namespace="/rtc")
        socket_b.disconnect(namespace="/rtc")

    def test_api_rejects_anonymous_and_cross_origin_mutations(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.post("/api/rtc/sessions", json={}).status_code, 401)
        response = self.alice.post(
            "/api/rtc/sessions",
            json={},
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_rtc_page_has_privacy_and_anti_clickjacking_headers(self):
        response = self.alice.get("/rtc")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("camera=(self)", response.headers["Permissions-Policy"])


if __name__ == "__main__":
    unittest.main()
