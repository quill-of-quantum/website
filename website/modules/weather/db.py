import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


DATA_DIR = Path(os.environ.get("WEATHER_DATA_DIR", Path(__file__).resolve().parents[2] / "data" / "weather"))
DB_PATH = Path(os.environ.get("WEATHER_DB_PATH", DATA_DIR / "weather.db"))
LEGACY_PATH = DATA_DIR / "number.txt"


@contextmanager
def connection(db_path=None):
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def initialize(db_path=None):
    with connection(db_path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                starts_at TEXT NOT NULL,
                ends_at TEXT,
                active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_weather_period
                ON periods(active) WHERE active=1;
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                value REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'shortcut',
                UNIQUE(recorded_at, value)
            );
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requested_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                status TEXT NOT NULL,
                trigger TEXT NOT NULL,
                period_id INTEGER,
                error TEXT,
                log_tail TEXT,
                FOREIGN KEY(period_id) REFERENCES periods(id)
            );
            CREATE TABLE IF NOT EXISTS weather_hourly (
                period_id INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                temperature REAL,
                source TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY(period_id, observed_at),
                FOREIGN KEY(period_id) REFERENCES periods(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS weather_daily (
                period_id INTEGER NOT NULL,
                observed_on TEXT NOT NULL,
                temperature_min REAL,
                temperature_max REAL,
                temperature_mean REAL,
                source TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY(period_id, observed_on),
                FOREIGN KEY(period_id) REFERENCES periods(id) ON DELETE CASCADE
            );
            """
        )
        columns = {row["name"] for row in db.execute("PRAGMA table_info(periods)")}
        migrations = {
            "location_name": "TEXT NOT NULL DEFAULT 'München'",
            "latitude": "REAL NOT NULL DEFAULT 48.14",
            "longitude": "REAL NOT NULL DEFAULT 11.58",
            "timezone": "TEXT NOT NULL DEFAULT 'Europe/Berlin'",
        }
        for name, definition in migrations.items():
            if name not in columns:
                db.execute(f"ALTER TABLE periods ADD COLUMN {name} {definition}")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        db.execute("INSERT OR IGNORE INTO settings(key,value,updated_at) VALUES('homepage_visible','1',?)", (now,))


def import_legacy_readings(path=None, db_path=None):
    initialize(db_path)
    source = Path(path or LEGACY_PATH)
    try:
        lines = [line.strip() for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    except FileNotFoundError:
        return 0
    rows = []
    for index in range(0, len(lines) - 1, 2):
        try:
            recorded = datetime.strptime(lines[index], "%Y年%m月%d日 %H:%M")
            rows.append((recorded.isoformat(timespec="minutes"), float(lines[index + 1]), "legacy"))
        except (ValueError, TypeError):
            continue
    with connection(db_path) as db:
        before = db.total_changes
        db.executemany("INSERT OR IGNORE INTO readings(recorded_at,value,source) VALUES(?,?,?)", rows)
        return db.total_changes - before


def ensure_default_period(db_path=None, legacy_path=None):
    initialize(db_path)
    # 正式数据库兼容旧 number.txt；临时/测试数据库只有明确传入来源时才导入。
    if db_path is None or legacy_path is not None:
        import_legacy_readings(path=legacy_path, db_path=db_path)
    with connection(db_path) as db:
        row = db.execute("SELECT * FROM periods WHERE active=1").fetchone()
        if row:
            return dict(row)
        first = db.execute("SELECT MIN(recorded_at) AS value FROM readings").fetchone()["value"]
        starts_at = (first or datetime.now().astimezone().date().isoformat())[:10]
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        db.execute(
            "INSERT INTO periods(name,starts_at,active,created_at) VALUES(?,?,1,?)",
            (f"周期 {starts_at}", starts_at, now),
        )
        return dict(db.execute("SELECT * FROM periods WHERE active=1").fetchone())


def list_periods(db_path=None):
    ensure_default_period(db_path)
    with connection(db_path) as db:
        return [dict(row) for row in db.execute("SELECT * FROM periods ORDER BY starts_at DESC,id DESC")]


def validate_location(location_name, latitude, longitude, timezone):
    label = str(location_name or "").strip()[:100]
    zone = str(timezone or "").strip()[:100]
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError) as exc:
        raise ValueError("经纬度必须是数字") from exc
    if not label:
        raise ValueError("地点名称不能为空")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("经纬度超出有效范围")
    if not zone or (zone != "auto" and "/" not in zone):
        raise ValueError("时区格式无效，例如 Europe/Berlin")
    return label, lat, lon, zone


def create_period(name, starts_at, db_path=None, location_name="München", latitude=48.14,
                  longitude=11.58, timezone="Europe/Berlin"):
    try:
        start = datetime.strptime(str(starts_at), "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("周期开始日期格式必须是 YYYY-MM-DD") from exc
    label = str(name or f"周期 {start}").strip()[:100]
    if not label:
        raise ValueError("周期名称不能为空")
    location_name, latitude, longitude, timezone = validate_location(
        location_name, latitude, longitude, timezone
    )
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with connection(db_path) as db:
        db.execute("UPDATE periods SET active=0 WHERE active=1")
        cursor = db.execute(
            "INSERT INTO periods(name,starts_at,active,created_at,location_name,latitude,longitude,timezone) "
            "VALUES(?,?,1,?,?,?,?,?)",
            (label, start, now, location_name, latitude, longitude, timezone),
        )
        return cursor.lastrowid


def update_period_location(period_id, location_name, latitude, longitude, timezone, db_path=None):
    location_name, latitude, longitude, timezone = validate_location(
        location_name, latitude, longitude, timezone
    )
    with connection(db_path) as db:
        row = db.execute(
            "SELECT id,location_name,latitude,longitude,timezone FROM periods WHERE id=?",
            (int(period_id),),
        ).fetchone()
        if not row:
            raise ValueError("指定周期不存在")
        changed = (
            row["location_name"] != location_name or float(row["latitude"]) != latitude
            or float(row["longitude"]) != longitude or row["timezone"] != timezone
        )
        db.execute(
            "UPDATE periods SET location_name=?,latitude=?,longitude=?,timezone=? WHERE id=?",
            (location_name, latitude, longitude, timezone, int(period_id)),
        )
        if changed:
            db.execute("DELETE FROM weather_hourly WHERE period_id=?", (int(period_id),))
            db.execute("DELETE FROM weather_daily WHERE period_id=?", (int(period_id),))
        return changed


def weather_cache_counts(period_id, db_path=None):
    with connection(db_path) as db:
        hourly = db.execute("SELECT COUNT(*) FROM weather_hourly WHERE period_id=?", (int(period_id),)).fetchone()[0]
        daily = db.execute("SELECT COUNT(*) FROM weather_daily WHERE period_id=?", (int(period_id),)).fetchone()[0]
    return {"hourly": hourly, "daily": daily}


def activate_period(period_id, db_path=None):
    with connection(db_path) as db:
        row = db.execute("SELECT id FROM periods WHERE id=?", (int(period_id),)).fetchone()
        if not row:
            raise ValueError("指定周期不存在")
        db.execute("UPDATE periods SET active=0 WHERE active=1")
        db.execute("UPDATE periods SET active=1 WHERE id=?", (int(period_id),))


def active_period(db_path=None):
    return ensure_default_period(db_path)


initialize()
