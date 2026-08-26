import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from modules.weather.db import active_period, connection, initialize, list_periods, weather_cache_counts


DATA_DIR = Path(os.environ.get("WEATHER_DATA_DIR", Path(__file__).resolve().parents[2] / "data" / "weather"))
REQUEST_PATH = DATA_DIR / "run_requested"


def get_config():
    initialize()
    with connection() as db:
        row = db.execute("SELECT value FROM settings WHERE key='homepage_visible'").fetchone()
    periods = list_periods()
    for period in periods:
        period["weather_cache"] = weather_cache_counts(period["id"])
    current = next((period for period in periods if period["active"]), active_period())
    return {
        "homepage_visible": not row or row["value"] == "1",
        "active_period": current,
        "periods": periods,
    }


def set_homepage_visible(value):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with connection() as db:
        db.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES('homepage_visible',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            ("1" if value else "0", now),
        )


def request_run(trigger="manual"):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = REQUEST_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps({"trigger": str(trigger)[:30]}), encoding="utf-8")
    os.replace(temporary, REQUEST_PATH)


def consume_run_request():
    try:
        payload = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
        REQUEST_PATH.unlink()
        return str(payload.get("trigger") or "manual")[:30]
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def start_analysis(trigger="manual"):
    """通过 oneshot systemd 单元立即启动一次分析。"""
    systemctl = shutil.which("systemctl", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    sudo = shutil.which("sudo", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if not systemctl:
        raise RuntimeError("找不到 systemctl")
    active = subprocess.run(
        [systemctl, "is-active", "--quiet", "weather.service"],
        capture_output=True, timeout=5, check=False,
    ).returncode == 0
    if active:
        raise RuntimeError("已有分析正在运行")
    request_run(trigger)
    command = [systemctl, "start", "--no-block", "weather.service"]
    if os.geteuid() != 0:
        if not sudo:
            REQUEST_PATH.unlink(missing_ok=True)
            raise RuntimeError("找不到 sudo，无法启动分析任务")
        command = [sudo, "-n", *command]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    if result.returncode != 0:
        REQUEST_PATH.unlink(missing_ok=True)
        raise RuntimeError((result.stderr or result.stdout or "无法启动分析任务").strip())


def latest_run():
    with connection() as db:
        row = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else {"status": "never"}
