"""Manual end-to-end check with two isolated Chromium browser contexts.

Run with the project's web environment. It uses only temporary accounts and a
loopback test server, then verifies that encrypted WebRTC media connects.
"""

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def serve(user_path, port):
    from flask import Flask
    from modules.auth import user_store
    from modules.auth.api import bp as auth_bp
    from modules.realtime import socketio
    from modules.rtc.api import bp as rtc_bp
    from modules.rtc import signaling as rtc_signaling  # noqa: F401

    user_store.USER_STORE_PATH = user_path
    user_store.create_user("alice", "test-password", "guest")
    user_store.create_user("bobby", "test-password", "guest")
    app = Flask(
        __name__,
        static_folder=str(PROJECT_ROOT / "static"),
        static_url_path="/static",
        template_folder=str(PROJECT_ROOT / "templates"),
    )
    app.secret_key = "rtc-browser-e2e-only"
    app.register_blueprint(auth_bp)
    app.register_blueprint(rtc_bp)
    socketio.init_app(app)
    socketio.run(app, host="127.0.0.1", port=port, log_output=False)


def available_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_server(origin):
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"{origin}/api/auth/status", timeout=0.5) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("RTC browser test server did not start")


def login(page, origin, username):
    page.goto(f"{origin}/rtc")
    result = page.evaluate("""async ({username}) => {
      const response = await fetch('/api/auth/login', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username, password: 'test-password'})
      });
      return {status: response.status, body: await response.json()};
    }""", {"username": username})
    if result["status"] != 200:
        raise RuntimeError(f"login failed: {result}")
    page.reload()


def run_browser_test():
    from playwright.sync_api import sync_playwright

    port = available_port()
    origin = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as temporary:
        server = subprocess.Popen(
            [sys.executable, __file__, "--server", os.path.join(temporary, "users.json"), str(port)],
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_for_server(origin)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    executable_path="/home/bbdwz/.cache/ms-playwright/chromium-1187/chrome-linux/chrome",
                    args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
                )
                context_a = browser.new_context(permissions=["camera", "microphone"])
                context_b = browser.new_context(permissions=["camera", "microphone"])
                page_a = context_a.new_page()
                page_b = context_b.new_page()
                page_a.on("pageerror", lambda error: print(f"page-a error: {error}"))
                page_b.on("pageerror", lambda error: print(f"page-b error: {error}"))
                login(page_a, origin, "alice")
                login(page_b, origin, "bobby")

                page_a.get_by_role("button", name="创建安全测试通话").click()
                page_a.locator("#inviteUrl").wait_for(state="visible", timeout=10_000)
                page_a.wait_for_function(
                    "document.querySelector('#inviteUrl').value.includes('#invite=')",
                    timeout=10_000,
                )
                invite = page_a.locator("#inviteUrl").input_value()
                if "#invite=" not in invite:
                    raise AssertionError(f"missing fragment invitation: {invite}")
                page_b.goto(invite)
                # The context was already on /rtc after login; force a document
                # load so the fragment invitation is processed like a shared link.
                page_b.reload()
                page_b.get_by_role("button", name="接受邀请并打开摄像头").click()
                try:
                    page_a.wait_for_function(
                        "document.querySelector('#rtcDiagnostics').textContent.includes('P2P 打洞直连')",
                        timeout=20_000,
                    )
                    page_b.wait_for_function(
                        "document.querySelector('#rtcDiagnostics').textContent.includes('P2P 打洞直连')",
                        timeout=20_000,
                    )
                except Exception:
                    print(f"page-a status: {page_a.locator('#rtcStatus').inner_text()}")
                    print(f"page-b status: {page_b.locator('#rtcStatus').inner_text()}")
                    print(f"page-a diagnostic: {page_a.locator('#rtcDiagnostics').inner_text()}")
                    print(f"page-b diagnostic: {page_b.locator('#rtcDiagnostics').inner_text()}")
                    raise
                page_a.wait_for_function(
                    "document.querySelector('#remoteVideo').srcObject?.getVideoTracks().length > 0",
                    timeout=10_000,
                )
                page_b.wait_for_function(
                    "document.querySelector('#remoteVideo').srcObject?.getVideoTracks().length > 0",
                    timeout=10_000,
                )
                for page in (page_a, page_b):
                    page.wait_for_function(
                        """() => {
                          const text = document.querySelector('#rtcDiagnostics').textContent;
                          return text.includes('发送画面') && !text.includes('发送 -- kbps');
                        }""",
                        timeout=10_000,
                    )
                print(page_a.locator("#rtcDiagnostics").inner_text())
                print(page_b.locator("#rtcDiagnostics").inner_text())
                browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--server":
        serve(sys.argv[2], int(sys.argv[3]))
    else:
        run_browser_test()
