import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta


DATA_DIR = "/home/bbdwz/projects/website/data/exchange"
DB_PATH = os.path.join(DATA_DIR, "exchange.db")
CACHE_PATH = os.path.join(DATA_DIR, "rates.json")
LEGACY_CACHE_PATH = "/home/bbdwz/projects/website/data/index/exchange_rate.json"
LEGACY_PLAN_PATH = os.path.join(DATA_DIR, "plan.json")
INDICATORS_PATH = os.path.join(DATA_DIR, "indicators.json")
SEASONALITY_PATH = os.path.join(DATA_DIR, "seasonality.json")
PATTERN_PATH = os.path.join(DATA_DIR, "pattern_model.json")

DEFAULT_PLAN = {
    "target_eur": 20000.0,
    "purchased_eur": 0.0,
    "start_date": date.today().isoformat(),
    "deadline": (date.today() + timedelta(days=365)).isoformat(),
    "cny_yield": 0.5,
}


@contextmanager
def connection():
    os.makedirs(DATA_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=10000")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def initialize():
    with connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS plan (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                target_eur REAL NOT NULL,
                purchased_eur REAL NOT NULL DEFAULT 0,
                start_date TEXT NOT NULL,
                deadline TEXT NOT NULL,
                cny_yield REAL NOT NULL DEFAULT 0.5,
                revision INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                executed_at TEXT NOT NULL,
                eur_amount REAL NOT NULL,
                rate REAL NOT NULL,
                cny_amount REAL NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analysis_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                quote_signature TEXT NOT NULL,
                plan_revision INTEGER NOT NULL,
                paths INTEGER NOT NULL,
                stable INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runtime_flags (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        existing = db.execute("SELECT 1 FROM plan WHERE id=1").fetchone()
        if not existing:
            legacy = _read_json(LEGACY_PLAN_PATH, {}) or {}
            plan = {**DEFAULT_PLAN, **legacy}
            now = _now()
            db.execute(
                "INSERT INTO plan(id,target_eur,purchased_eur,start_date,deadline,cny_yield,updated_at) VALUES(1,?,?,?,?,?,?)",
                (plan["target_eur"], plan["purchased_eur"], plan.get("start_date", DEFAULT_PLAN["start_date"]),
                 plan["deadline"], plan["cny_yield"], now),
            )
            _set_flag(db, "analysis_dirty", "1")


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def _set_flag(db, key, value):
    db.execute(
        "INSERT INTO runtime_flags(key,value,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
        (key, str(value), _now()),
    )


def load_rates():
    return _read_json(CACHE_PATH) or _read_json(LEGACY_CACHE_PATH)


def save_rates(payload):
    previous = load_rates() or {}
    _write_json(CACHE_PATH, payload)
    signature_changed = quote_signature(previous) != quote_signature(payload)
    if signature_changed:
        initialize()
        mark_analysis_dirty()
    return signature_changed


def load_indicators():
    return _read_json(INDICATORS_PATH)


def save_indicators(payload):
    _write_json(INDICATORS_PATH, payload)


def load_seasonality():
    return _read_json(SEASONALITY_PATH)


def save_seasonality(payload):
    _write_json(SEASONALITY_PATH, payload)


def load_pattern_report():
    return _read_json(PATTERN_PATH)


def save_pattern_report(payload):
    _write_json(PATTERN_PATH, payload)


def quote_signature(payload):
    if not payload:
        return "missing"
    dates = payload.get("dates") or []
    eur = payload.get("eur_cny") or []
    usd = payload.get("usd_cny") or []
    parts = (payload.get("latest_date"), payload.get("latest_eur"), payload.get("latest_usd"),
             dates[-1] if dates else None, eur[-1] if eur else None, usd[-1] if usd else None)
    return "|".join(str(value or "") for value in parts)


def load_plan():
    initialize()
    with connection() as db:
        row = db.execute("SELECT * FROM plan WHERE id=1").fetchone()
    return dict(row)


def save_plan(plan):
    initialize()
    with connection() as db:
        current = db.execute("SELECT revision,start_date FROM plan WHERE id=1").fetchone()
        revision = int(current["revision"]) + 1
        db.execute(
            "UPDATE plan SET target_eur=?,purchased_eur=?,start_date=?,deadline=?,cny_yield=?,revision=?,updated_at=? WHERE id=1",
            (plan["target_eur"], plan["purchased_eur"], plan.get("start_date") or current["start_date"],
             plan["deadline"], plan["cny_yield"], revision, _now()),
        )
        _set_flag(db, "analysis_dirty", "1")
    return load_plan()


def add_execution(eur_amount, rate, executed_at=None, note=""):
    initialize()
    cny_amount = round(eur_amount * rate, 2)
    executed_at = executed_at or _now()
    with connection() as db:
        plan = db.execute("SELECT target_eur,purchased_eur,revision FROM plan WHERE id=1").fetchone()
        if plan["purchased_eur"] + eur_amount > plan["target_eur"] + 1e-9:
            raise ValueError("本次兑换超过剩余目标")
        cursor = db.execute(
            "INSERT INTO executions(executed_at,eur_amount,rate,cny_amount,note,created_at) VALUES(?,?,?,?,?,?)",
            (executed_at, eur_amount, rate, cny_amount, note.strip(), _now()),
        )
        db.execute(
            "UPDATE plan SET purchased_eur=purchased_eur+?,revision=revision+1,updated_at=? WHERE id=1",
            (eur_amount, _now()),
        )
        _set_flag(db, "analysis_dirty", "1")
        execution_id = cursor.lastrowid
    return get_execution(execution_id)


def get_execution(execution_id):
    initialize()
    with connection() as db:
        row = db.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
    return dict(row) if row else None


def list_executions(limit=100):
    initialize()
    with connection() as db:
        rows = db.execute("SELECT * FROM executions ORDER BY executed_at DESC,id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]


def mark_analysis_dirty():
    initialize()
    with connection() as db:
        _set_flag(db, "analysis_dirty", "1")


def analysis_is_dirty():
    initialize()
    with connection() as db:
        row = db.execute("SELECT value FROM runtime_flags WHERE key='analysis_dirty'").fetchone()
        latest = db.execute("SELECT created_at FROM analysis_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    if not row or row["value"] == "1" or not latest:
        return True
    try:
        age = datetime.now().astimezone() - datetime.fromisoformat(latest["created_at"])
        return age.total_seconds() >= 3600
    except ValueError:
        return True


def save_analysis(payload):
    initialize()
    with connection() as db:
        db.execute(
            "INSERT INTO analysis_snapshots(created_at,quote_signature,plan_revision,paths,stable,payload_json) VALUES(?,?,?,?,?,?)",
            (_now(), payload.get("quote_signature", ""), payload["plan"].get("revision", 0),
             payload["simulation"]["paths"], int(payload["simulation"]["stable"]), json.dumps(payload, ensure_ascii=False)),
        )
        db.execute("DELETE FROM analysis_snapshots WHERE id NOT IN (SELECT id FROM analysis_snapshots ORDER BY id DESC LIMIT 200)")
        _set_flag(db, "analysis_dirty", "0")


def load_latest_analysis():
    initialize()
    with connection() as db:
        row = db.execute("SELECT payload_json FROM analysis_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row["payload_json"]) if row else None


initialize()
