import unittest

try:
    from flask import Flask
    from modules.url_probe.api import MAX_SPEED_TEST_BYTES, bp
except ModuleNotFoundError:
    Flask = None
    MAX_SPEED_TEST_BYTES = None
    bp = None
from modules.url_probe.service import build_targets


class BuildTargetsTest(unittest.TestCase):
    def test_numeric_rule_builds_inclusive_range(self):
        self.assertEqual(build_targets("www.example.com", {
            "mode": "numeric",
            "template": "pages/{n}.html",
            "start": 1,
            "end": 3,
            "step": 1,
        }), [
            "https://www.example.com/pages/1.html",
            "https://www.example.com/pages/2.html",
            "https://www.example.com/pages/3.html",
        ])


    def test_custom_rule_accepts_paths_and_full_urls(self):
        self.assertEqual(build_targets("https://example.com/root", {
            "mode": "custom",
            "entries": "one\n/two\nhttp://example.org/three",
        }), [
            "https://example.com/root/one",
            "https://example.com/root/two",
            "http://example.org/three",
        ])


    def test_numeric_rule_requires_placeholder(self):
        with self.assertRaisesRegex(ValueError, r"\{n\}"):
            build_targets("example.com", {"mode": "numeric", "template": "page"})


@unittest.skipUnless(Flask, "需要 Flask 运行环境")
class SpeedTestApiTest(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(bp)
        self.client = app.test_client()

    def test_ping(self):
        response = self.client.get("/api/url-probe/speed/ping")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

    def test_download_returns_requested_bytes_without_cache(self):
        response = self.client.get("/api/url-probe/speed/download?bytes=1024")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1024)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_download_rejects_oversize_request(self):
        response = self.client.get(f"/api/url-probe/speed/download?bytes={MAX_SPEED_TEST_BYTES + 1}")
        self.assertEqual(response.status_code, 400)

    def test_upload_reports_received_bytes(self):
        response = self.client.post(
            "/api/url-probe/speed/upload",
            data=b"x" * 1024,
            content_type="application/octet-stream",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["received_bytes"], 1024)


if __name__ == "__main__":
    unittest.main()
