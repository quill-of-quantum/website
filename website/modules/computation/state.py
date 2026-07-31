import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime


DATA_DIR = "/home/bbdwz/projects/website/data/computation"
DB_PATH = os.path.join(DATA_DIR, "computation.db")


@contextmanager
def connection():
    os.makedirs(DATA_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def initialize():
    with connection() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS job_state (
                name TEXT PRIMARY KEY,
                last_started_at TEXT,
                last_success_at TEXT,
                last_finished_at TEXT,
                duration_seconds REAL,
                status TEXT NOT NULL DEFAULT 'never',
                last_error TEXT,
                result_json TEXT
            )"""
        )


def get_job_state(name):
    initialize()
    with connection() as db:
        row = db.execute("SELECT * FROM job_state WHERE name=?", (name,)).fetchone()
    return dict(row) if row else None


def mark_started(name, started_at):
    initialize()
    with connection() as db:
        db.execute(
            "INSERT INTO job_state(name,last_started_at,status) VALUES(?,?,'running') "
            "ON CONFLICT(name) DO UPDATE SET last_started_at=excluded.last_started_at,status='running',last_error=NULL",
            (name, started_at.isoformat(timespec="seconds")),
        )


def mark_finished(name, started_at, result=None, error=None):
    now = datetime.now().astimezone()
    duration = (now - started_at).total_seconds()
    with connection() as db:
        db.execute(
            "UPDATE job_state SET last_success_at=CASE WHEN ? IS NULL THEN ? ELSE last_success_at END," 
            "last_finished_at=?,duration_seconds=?,status=?,last_error=?,result_json=? WHERE name=?",
            (error, now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), duration,
             "failed" if error else "success", error, json.dumps(result, ensure_ascii=False, default=str), name),
        )


def list_job_states():
    initialize()
    with connection() as db:
        rows = db.execute("SELECT * FROM job_state ORDER BY name").fetchall()
    return [dict(row) for row in rows]


initialize()
