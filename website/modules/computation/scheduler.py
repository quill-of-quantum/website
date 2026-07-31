#!/usr/bin/env python3
import signal
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules.computation.registry import registered_jobs
from modules.computation.state import get_job_state, mark_finished, mark_started


running = True


def stop(_signum, _frame):
    global running
    running = False


def is_due(job, now):
    state = get_job_state(job.name)
    if not state or not state.get("last_started_at"):
        return job.run_on_start
    try:
        last = datetime.fromisoformat(state["last_started_at"])
        retry_seconds = min(job.interval_seconds, 60) if state.get("status") == "failed" else job.interval_seconds
        return (now - last).total_seconds() >= retry_seconds
    except ValueError:
        return True


def run_job(job):
    started = datetime.now().astimezone()
    mark_started(job.name, started)
    print(f"[{started.isoformat(timespec='seconds')}] 开始 {job.name}", flush=True)
    try:
        result = job.function()
        mark_finished(job.name, started, result=result)
        print(f"完成 {job.name}: {result}", flush=True)
    except Exception as exc:
        mark_finished(job.name, started, error=f"{type(exc).__name__}: {exc}")
        print(f"失败 {job.name}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def main(once=False):
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    jobs = registered_jobs()
    while running:
        now = datetime.now().astimezone()
        for job in jobs:
            if once or is_due(job, now):
                run_job(job)
        if once:
            return
        time.sleep(1)


if __name__ == "__main__":
    main(once="--once" in sys.argv)
