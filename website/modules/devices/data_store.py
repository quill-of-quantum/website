"""Unified persistent storage for sensor readings and device photos."""

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

BASE_DIR = "/home/bbdwz/projects/website/data/devices"
PHOTO_ROOT = os.path.join(BASE_DIR, "photos")
DATABASE_PATH = os.path.join(BASE_DIR, "device_data.sqlite3")
LEGACY_PHOTO_DB_PATH = os.path.join(BASE_DIR, "photos.sqlite3")
LOCK = threading.RLock()
_MIGRATED = set()

CATEGORY_LABELS = {
    "photo": "照片",
    "temperature": "温度",
    "humidity": "湿度",
    "battery": "电量",
    "telemetry": "通用数据",
    "wakeup_reason": "唤醒原因",
    "free_heap_kb": "可用内存",
    "psram_free_kb": "可用 PSRAM",
}


def configure(base_dir, photo_root=None, database_path=None, legacy_photo_db_path=None):
    global BASE_DIR, PHOTO_ROOT, DATABASE_PATH, LEGACY_PHOTO_DB_PATH
    BASE_DIR = base_dir
    PHOTO_ROOT = photo_root or os.path.join(base_dir, "photos")
    DATABASE_PATH = database_path or os.path.join(base_dir, "device_data.sqlite3")
    LEGACY_PHOTO_DB_PATH = legacy_photo_db_path or os.path.join(base_dir, "photos.sqlite3")


def _connect():
    os.makedirs(BASE_DIR, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    for path in (DATABASE_PATH,DATABASE_PATH+"-wal",DATABASE_PATH+"-shm"):
        try: os.chmod(path,0o660)
        except (FileNotFoundError,PermissionError): pass
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS packets (
            packet_id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            device_name TEXT NOT NULL,
            device_type TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            device_deleted_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            packet_id TEXT NOT NULL REFERENCES packets(packet_id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            record_key TEXT NOT NULL,
            measured_at TEXT NOT NULL,
            measured_at_source TEXT NOT NULL,
            received_at INTEGER NOT NULL,
            value_json TEXT,
            photo_id TEXT,
            width INTEGER,
            height INTEGER,
            size_bytes INTEGER,
            relative_path TEXT,
            UNIQUE(packet_id, category, record_key)
        );
        CREATE INDEX IF NOT EXISTS records_packet_category_time
        ON records(packet_id, category, received_at DESC, id DESC);
    """)
    return connection


def _packet_values(record):
    now = int(time.time())
    return (
        str(record.get("id")),
        str(record.get("device_id") or "unknown"),
        str(record.get("name") or record.get("device_id") or "未命名设备")[:120],
        str(record.get("device_type") or "generic")[:80],
        int(record.get("created_at") or now),
        now,
    )


def _ensure_packet(connection, record):
    values = _packet_values(record)
    connection.execute("""
        INSERT INTO packets(packet_id,device_id,device_name,device_type,created_at,updated_at,device_deleted_at)
        VALUES(?,?,?,?,?,?,NULL)
        ON CONFLICT(packet_id) DO UPDATE SET
            device_id=excluded.device_id,
            device_name=excluded.device_name,
            device_type=excluded.device_type,
            updated_at=excluded.updated_at,
            device_deleted_at=NULL
    """, values)
    return values[0]


def sync_device(record):
    with LOCK:
        connection = _connect()
        try:
            with connection:
                _ensure_packet(connection, record)
        finally:
            connection.close()


def mark_device_deleted(record):
    with LOCK:
        connection = _connect()
        try:
            with connection:
                packet_id = _ensure_packet(connection, record)
                connection.execute("UPDATE packets SET device_deleted_at=?,updated_at=? WHERE packet_id=?", (int(time.time()), int(time.time()), packet_id))
        finally:
            connection.close()


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _measurement_time(payload):
    value = str(payload.get("measured_at") or "").strip() if isinstance(payload, dict) else ""
    return (value, "device") if value else (_utc_now_iso(), "server")


def _category_for(field):
    return {"temperature_c":"temperature", "humidity_percent":"humidity", "battery_percent":"battery"}.get(field, field)


def _safe_category(value):
    value = "".join(char if char.isalnum() or char in "_.-" else "_" for char in str(value).lower()).strip("_.-")
    return value[:80] or "telemetry"


def record_telemetry(record, payload):
    payload = payload if isinstance(payload, dict) else {}
    sample_id = str(payload.get("sample_id") or uuid.uuid4().hex)[:128]
    measured_at, source = _measurement_time(payload)
    received_at = int(time.time())
    values = dict(payload.get("data")) if isinstance(payload.get("data"), dict) else {}
    if "battery_percent" in payload:
        values["battery_percent"] = payload["battery_percent"]
    if not values:
        values = {"telemetry": payload}
    inserted = 0
    with LOCK:
        connection = _connect()
        try:
            with connection:
                packet_id = _ensure_packet(connection, record)
                for field, value in values.items():
                    category = _safe_category(_category_for(field))
                    key = f"{sample_id}:{field}"
                    cursor = connection.execute("""
                        INSERT OR IGNORE INTO records(packet_id,category,record_key,measured_at,measured_at_source,received_at,value_json)
                        VALUES(?,?,?,?,?,?,?)
                    """, (packet_id, category, key, measured_at, source, received_at, json.dumps({"field":field,"value":value}, ensure_ascii=False, separators=(",",":"))))
                    inserted += cursor.rowcount
        finally:
            connection.close()
    return inserted


def _record_public(row):
    item = dict(row)
    if item.get("value_json"):
        try: item["value"] = json.loads(item.pop("value_json"))
        except (TypeError, json.JSONDecodeError): item["value"] = None
    else:
        item.pop("value_json", None)
    item.pop("relative_path", None)
    item["category_label"] = CATEGORY_LABELS.get(item.get("category"), item.get("category"))
    if item.get("category") == "photo":
        item["captured_at"] = item.get("measured_at")
        item["captured_at_source"] = item.get("measured_at_source")
        item["url"] = f"/1/api/devices/database/packets/{quote(str(item['packet_id']),safe='')}/records/{item['id']}/file"
    return item


def record_photo(record, photo_id, payload, photo_info, captured_at, captured_at_source):
    with LOCK:
        connection = _connect()
        target = None
        try:
            with connection:
                packet_id = _ensure_packet(connection, record)
                existing = connection.execute("SELECT * FROM records WHERE packet_id=? AND category='photo' AND record_key=?", (packet_id, photo_id)).fetchone()
                if existing is not None:
                    return _record_public(existing), True
                directory = os.path.join(PHOTO_ROOT, str(record.get("device_id")), captured_at[:10])
                os.makedirs(directory, exist_ok=True)
                target = os.path.join(directory, f"{photo_id}.jpg")
                temporary = f"{target}.{uuid.uuid4().hex}.tmp"
                with open(temporary, "xb") as handle:
                    handle.write(payload); handle.flush(); os.fsync(handle.fileno())
                os.replace(temporary, target)
                relative_path = os.path.relpath(target, BASE_DIR)
                cursor = connection.execute("""
                    INSERT INTO records(packet_id,category,record_key,measured_at,measured_at_source,received_at,photo_id,width,height,size_bytes,relative_path)
                    VALUES(?,'photo',?,?,?,?,?,?,?,?,?)
                """, (packet_id, photo_id, captured_at, captured_at_source, int(time.time()), photo_id, int(photo_info["width"]), int(photo_info["height"]), int(photo_info["size_bytes"]), relative_path))
                row = connection.execute("SELECT * FROM records WHERE id=?", (cursor.lastrowid,)).fetchone()
                return _record_public(row), False
        except Exception:
            if target:
                try: os.unlink(target)
                except OSError: pass
            raise
        finally:
            connection.close()


def list_photos(packet_id, limit=12):
    connection = _connect()
    try:
        rows = connection.execute("SELECT * FROM records WHERE packet_id=? AND category='photo' ORDER BY received_at DESC,id DESC LIMIT ?", (str(packet_id), max(1, min(int(limit), 100)))).fetchall()
        return [_record_public(row) for row in rows]
    finally:
        connection.close()


def get_photo(packet_id, record_id):
    connection = _connect()
    try:
        row = connection.execute("SELECT * FROM records WHERE packet_id=? AND id=? AND category='photo'", (str(packet_id), int(record_id))).fetchone()
    finally:
        connection.close()
    if row is None:
        return None, None
    metadata = dict(row)
    path = os.path.realpath(os.path.join(BASE_DIR, metadata["relative_path"]))
    root = os.path.realpath(PHOTO_ROOT) + os.sep
    if not path.startswith(root) or not os.path.isfile(path):
        return None, None
    return _record_public(row), path


def list_packets():
    connection = _connect()
    try:
        packets = []
        for row in connection.execute("SELECT * FROM packets ORDER BY updated_at DESC,created_at DESC").fetchall():
            item = dict(row)
            counts = connection.execute("SELECT category,COUNT(*) AS count,MAX(received_at) AS latest_at FROM records WHERE packet_id=? GROUP BY category ORDER BY category", (item["packet_id"],)).fetchall()
            item["categories"] = [{"id":entry["category"],"label":CATEGORY_LABELS.get(entry["category"],entry["category"]),"count":entry["count"],"latest_at":entry["latest_at"]} for entry in counts]
            item["record_count"] = sum(entry["count"] for entry in counts)
            packets.append(item)
        return packets
    finally:
        connection.close()


def packet_detail(packet_id, limit_per_category=100):
    connection = _connect()
    try:
        packet = connection.execute("SELECT * FROM packets WHERE packet_id=?", (str(packet_id),)).fetchone()
        if packet is None: return None
        item = dict(packet); item["categories"] = []
        categories = connection.execute("SELECT category,COUNT(*) AS count FROM records WHERE packet_id=? GROUP BY category ORDER BY category", (str(packet_id),)).fetchall()
        for category in categories:
            rows = connection.execute("SELECT * FROM records WHERE packet_id=? AND category=? ORDER BY received_at DESC,id DESC LIMIT ?", (str(packet_id), category["category"], max(1,min(int(limit_per_category),500)))).fetchall()
            item["categories"].append({"id":category["category"],"label":CATEGORY_LABELS.get(category["category"],category["category"]),"count":category["count"],"records":[_record_public(row) for row in rows],"has_more":category["count"]>len(rows)})
        return item
    finally:
        connection.close()


def category_records(packet_id, category, limit=100, offset=0):
    connection = _connect()
    try:
        limit=max(1,min(int(limit),500)); offset=max(0,int(offset))
        total=connection.execute("SELECT COUNT(*) FROM records WHERE packet_id=? AND category=?",(str(packet_id),str(category))).fetchone()[0]
        rows=connection.execute("SELECT * FROM records WHERE packet_id=? AND category=? ORDER BY received_at DESC,id DESC LIMIT ? OFFSET ?",(str(packet_id),str(category),limit,offset)).fetchall()
        return {"records":[_record_public(row) for row in rows],"total":total,"offset":offset,"has_more":offset+len(rows)<total}
    finally:
        connection.close()


def _delete_files(rows):
    for row in rows:
        relative = row["relative_path"]
        if not relative: continue
        path = os.path.realpath(os.path.join(BASE_DIR, relative)); root = os.path.realpath(PHOTO_ROOT) + os.sep
        if path.startswith(root):
            try: os.unlink(path)
            except FileNotFoundError: pass


def delete_category(packet_id, category):
    with LOCK:
        connection = _connect()
        try:
            rows = connection.execute("SELECT relative_path FROM records WHERE packet_id=? AND category=?", (str(packet_id), str(category))).fetchall()
            with connection:
                cursor = connection.execute("DELETE FROM records WHERE packet_id=? AND category=?", (str(packet_id), str(category)))
                connection.execute("UPDATE packets SET updated_at=? WHERE packet_id=?", (int(time.time()), str(packet_id)))
            _delete_files(rows)
            return cursor.rowcount
        finally:
            connection.close()


def delete_packet(packet_id):
    with LOCK:
        connection = _connect()
        try:
            rows = connection.execute("SELECT relative_path FROM records WHERE packet_id=?", (str(packet_id),)).fetchall()
            with connection:
                cursor = connection.execute("DELETE FROM packets WHERE packet_id=?", (str(packet_id),))
            _delete_files(rows)
            return cursor.rowcount > 0
        finally:
            connection.close()


def migrate_legacy(devices):
    key = (DATABASE_PATH, LEGACY_PHOTO_DB_PATH)
    if key in _MIGRATED: return
    with LOCK:
        if key in _MIGRATED: return
        approved = [record for record in devices.values() if record.get("status")=="approved"]
        device_map = {str(record.get("device_id")):record for record in approved}
        connection = _connect()
        try:
            flags={row["key"]:row["value"] for row in connection.execute("SELECT key,value FROM metadata").fetchall()}
            with connection:
                for record in approved:
                    _ensure_packet(connection, record)
            if flags.get("legacy_last_telemetry_migrated")!="1":
                for record in approved:
                    last = record.get("last_telemetry")
                    if isinstance(last, dict) and isinstance(last.get("data"), dict):
                        payload=dict(last["data"])
                        payload.setdefault("sample_id",f"legacy-{record.get('id')}-{last.get('received_at',0)}")
                        record_telemetry(record,payload)
                with connection:
                    connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('legacy_last_telemetry_migrated','1')")
            if flags.get("legacy_photos_migrated")!="1" and os.path.exists(LEGACY_PHOTO_DB_PATH) and os.path.realpath(LEGACY_PHOTO_DB_PATH) != os.path.realpath(DATABASE_PATH):
                legacy = sqlite3.connect(LEGACY_PHOTO_DB_PATH); legacy.row_factory = sqlite3.Row
                try:
                    rows = legacy.execute("SELECT * FROM photos ORDER BY id").fetchall()
                except sqlite3.DatabaseError:
                    rows = []
                finally:
                    legacy.close()
                with connection:
                    for row in rows:
                        old = dict(row); record = device_map.get(str(old.get("device_id")))
                        if record is None:
                            packet_id = "legacy-" + uuid.uuid5(uuid.NAMESPACE_URL, str(old.get("device_id"))).hex
                            record = {"id":packet_id,"device_id":old.get("device_id") or "unknown","name":old.get("device_id") or "已删除设备","device_type":"unknown","created_at":old.get("received_at")}
                            _ensure_packet(connection, record)
                            connection.execute("UPDATE packets SET device_deleted_at=? WHERE packet_id=?", (int(time.time()), packet_id))
                        packet_id = str(record.get("id"))
                        connection.execute("""
                            INSERT OR IGNORE INTO records(packet_id,category,record_key,measured_at,measured_at_source,received_at,photo_id,width,height,size_bytes,relative_path)
                            VALUES(?,'photo',?,?,?,?,?,?,?,?,?)
                        """, (packet_id,old["photo_id"],old["captured_at"],old["captured_at_source"],old["received_at"],old["photo_id"],old["width"],old["height"],old["size_bytes"],old["relative_path"]))
                    connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('legacy_photos_migrated','1')")
            elif flags.get("legacy_photos_migrated")!="1":
                with connection:
                    connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('legacy_photos_migrated','1')")
            _MIGRATED.add(key)
        finally:
            connection.close()
