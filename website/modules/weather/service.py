#!/usr/bin/env python3
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from modules.weather.db import active_period, connection, import_legacy_readings
from modules.weather.store import consume_run_request


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANALYZE_SCRIPT = PROJECT_ROOT / "modules" / "weather" / "analyze.py"
def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_once(trigger="manual"):
    import_legacy_readings()
    period = active_period()
    with connection() as db:
        cursor = db.execute(
            "INSERT INTO runs(requested_at,started_at,status,trigger,period_id) VALUES(?,?,'running',?,?)",
            (_now(), _now(), trigger, period["id"]),
        )
        run_id = cursor.lastrowid
    env = os.environ.copy()
    env["WEATHER_PERIOD_START"] = period["starts_at"]
    try:
        result = subprocess.run(
            [sys.executable, str(ANALYZE_SCRIPT)], cwd=PROJECT_ROOT, env=env,
            capture_output=True, text=True, timeout=600, check=False,
        )
        output = ((result.stdout or "") + ("\n" + result.stderr if result.stderr else ""))[-20000:]
        status = "success" if result.returncode == 0 else "failed"
        error = None if result.returncode == 0 else f"分析进程退出码 {result.returncode}"
    except Exception as exc:
        output, status, error = "", "failed", f"{type(exc).__name__}: {exc}"
    with connection() as db:
        db.execute(
            "UPDATE runs SET finished_at=?,status=?,error=?,log_tail=? WHERE id=?",
            (_now(), status, error, output, run_id),
        )
    return status == "success"


def main():
    trigger = consume_run_request() or "systemd"
    return 0 if run_once(trigger) else 1


if __name__ == "__main__":
    raise SystemExit(main())
