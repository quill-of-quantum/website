import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modules.housing.store import DATA_DIR, load_state


DB_PATH = DATA_DIR / "housing.db"
CHANGE_DISPLAY_RETENTION = timedelta(hours=8)


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS rooms (
          id TEXT PRIMARY KEY, rental_type TEXT NOT NULL, url TEXT NOT NULL,
          summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
          first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, delisted_at TEXT NOT NULL DEFAULT '',
          detail_json TEXT NOT NULL DEFAULT '{}', latitude REAL, longitude REAL
        );
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL,
          started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
          status TEXT NOT NULL, total INTEGER NOT NULL DEFAULT 0,
          added INTEGER NOT NULL DEFAULT 0, updated INTEGER NOT NULL DEFAULT 0,
          delisted INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS changes (
          id INTEGER PRIMARY KEY AUTOINCREMENT, room_id TEXT NOT NULL,
          change_type TEXT NOT NULL, rental_type TEXT NOT NULL,
          happened_at TEXT NOT NULL, snapshot_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS geocode_cache (
          address TEXT PRIMARY KEY, result_json TEXT NOT NULL, queried_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rooms_status ON rooms(status);
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(rooms)")}
    change_timestamp_added = "record_change_at" not in columns
    for name, declaration in {
        "address": "TEXT NOT NULL DEFAULT ''", "room_type_text": "TEXT NOT NULL DEFAULT ''",
        "geocode_result": "TEXT NOT NULL DEFAULT ''", "coordinate_accuracy": "TEXT NOT NULL DEFAULT ''",
        "record_change": "TEXT NOT NULL DEFAULT '未变化（复用）'",
        "record_change_at": "TEXT NOT NULL DEFAULT ''",
    }.items():
        if name not in columns:
            db.execute(f"ALTER TABLE rooms ADD COLUMN {name} {declaration}")
    db.execute("CREATE INDEX IF NOT EXISTS idx_rooms_address ON rooms(address)")
    if change_timestamp_added:
        # 一次性兼容已有数据库：从变化历史恢复最近一次变化时间及仍在展示期内的标签。
        db.execute("""UPDATE rooms SET record_change_at=COALESCE((
            SELECT happened_at FROM changes WHERE changes.room_id=rooms.id ORDER BY happened_at DESC LIMIT 1
        ), '') WHERE record_change_at=''""")
        cutoff = (datetime.now(timezone.utc) - CHANGE_DISPLAY_RETENTION).isoformat().replace("+00:00", "Z")
        db.execute("""UPDATE rooms SET record_change=CASE (
            SELECT change_type FROM changes WHERE changes.room_id=rooms.id ORDER BY happened_at DESC LIMIT 1
        ) WHEN 'added' THEN '新上架' WHEN 'relisted' THEN '重新上架'
          WHEN 'updated' THEN '已更新/重新上架' WHEN 'delisted' THEN '已下架'
          ELSE record_change END
          WHERE record_change='未变化（复用）' AND record_change_at>=?""", (cutoff,))
    return db


def _display_change(old, change, now):
    if change == "added":
        return "新上架", now
    if change == "relisted":
        return "重新上架", now
    if change == "updated":
        return "已更新/重新上架", now
    if change == "delisted":
        return "已下架", now
    previous_label = old.get("record_change") or "未变化（复用）"
    previous_at = old.get("record_change_at") or ""
    try:
        changed_at = datetime.fromisoformat(previous_at.replace("Z", "+00:00"))
        current_at = datetime.fromisoformat(now.replace("Z", "+00:00"))
        if current_at - changed_at < CHANGE_DISPLAY_RETENTION:
            return previous_label, previous_at
    except (TypeError, ValueError):
        pass
    return "未变化（复用）", previous_at


def migrate_legacy_state():
    with connect() as db:
        if db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]:
            return 0
        rooms = (load_state().get("rooms") or {}).values()
        now = _now()
        for room in rooms:
            db.execute(
                "INSERT OR IGNORE INTO rooms(id,rental_type,url,summary,status,first_seen_at,last_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (room.get("id"), room.get("rental_type") or "unknown", room.get("url") or "",
                 room.get("summary") or "", "active", now, now, now),
            )
        return db.total_changes


def reset_tracking_data():
    """清空房源业务历史但保留 geocode_cache，供管理员建立全新基准。"""
    with connect() as db:
        db.execute("DELETE FROM rooms")
        db.execute("DELETE FROM changes")
        db.execute("DELETE FROM runs")
        # 兼容曾短暂存在过的通知历史表；地理编码缓存明确不删除。
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_history'").fetchone():
            db.execute("DELETE FROM notification_history")


def apply_catalog(current, mode, started_at, baseline=False):
    now = _now()
    changes = []
    with connect() as db:
        existing = {row["id"]: dict(row) for row in db.execute("SELECT * FROM rooms")}
        for room_id, room in current.items():
            old = existing.get(room_id)
            if old is None:
                change = "" if baseline else "added"
                display_label = "未变化（复用）" if baseline else "新上架"
                display_at = "" if baseline else now
                db.execute("""INSERT INTO rooms(id,rental_type,url,summary,status,first_seen_at,last_seen_at,updated_at,
                    detail_json,address,room_type_text,latitude,longitude,geocode_result,coordinate_accuracy,record_change,record_change_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (room_id, room["rental_type"], room["url"], room.get("summary", ""), "active", now, now, now,
                     json.dumps(room.get("detail") or {}, ensure_ascii=False), room.get("address", ""), room.get("room_type_text", ""),
                     room.get("latitude"), room.get("longitude"), room.get("geocode_result", ""), room.get("coordinate_accuracy", ""), display_label, display_at))
            else:
                if old["status"] != "active":
                    change = "relisted"
                elif old["summary"] != room.get("summary", ""):
                    change = "updated"
                else:
                    change = ""
                display_change, display_change_at = _display_change(old, change, now)
                db.execute(
                    """UPDATE rooms SET rental_type=?,url=?,summary=?,status='active',last_seen_at=?,updated_at=?,delisted_at='',
                    detail_json=CASE WHEN ?!='{}' THEN ? ELSE detail_json END,
                    address=CASE WHEN ?!='' THEN ? ELSE address END, room_type_text=CASE WHEN ?!='' THEN ? ELSE room_type_text END,
                    latitude=COALESCE(?,latitude),longitude=COALESCE(?,longitude),
                    geocode_result=CASE WHEN ?!='' THEN ? ELSE geocode_result END,
                    coordinate_accuracy=CASE WHEN ?!='' THEN ? ELSE coordinate_accuracy END,
                    record_change=?,record_change_at=? WHERE id=?""",
                    (room["rental_type"], room["url"], room.get("summary", ""), now, now if change else old["updated_at"],
                     json.dumps(room.get("detail") or {}, ensure_ascii=False), json.dumps(room.get("detail") or {}, ensure_ascii=False),
                     room.get("address", ""), room.get("address", ""), room.get("room_type_text", ""), room.get("room_type_text", ""),
                     room.get("latitude"), room.get("longitude"), room.get("geocode_result", ""), room.get("geocode_result", ""),
                     room.get("coordinate_accuracy", ""), room.get("coordinate_accuracy", ""),
                     display_change, display_change_at, room_id),
                )
            if change:
                item = {**room, "change": change, "recorded_at": now}
                changes.append(item)
                db.execute("INSERT INTO changes(room_id,change_type,rental_type,happened_at,snapshot_json) VALUES(?,?,?,?,?)",
                           (room_id, change, room["rental_type"], now, json.dumps(item, ensure_ascii=False)))
        for room_id, old in existing.items():
            if room_id not in current and old["status"] == "active":
                item = {"id": room_id, "url": old["url"], "rental_type": old["rental_type"], "summary": old["summary"], "change": "delisted", "recorded_at": now}
                changes.append(item)
                db.execute("UPDATE rooms SET status='delisted',delisted_at=?,updated_at=?,record_change='已下架',record_change_at=? WHERE id=?", (now, now, now, room_id))
                db.execute("INSERT INTO changes(room_id,change_type,rental_type,happened_at,snapshot_json) VALUES(?,?,?,?,?)",
                           (room_id, "delisted", old["rental_type"], now, json.dumps(item, ensure_ascii=False)))
        # 展示标签只在正常搜索时顺便过期，不运行额外计时器；也覆盖长期保持下架的房源。
        display_cutoff = (datetime.fromisoformat(now.replace("Z", "+00:00")) - CHANGE_DISPLAY_RETENTION).isoformat().replace("+00:00", "Z")
        db.execute(
            """UPDATE rooms SET record_change='未变化（复用）'
               WHERE record_change!='未变化（复用）' AND record_change_at!='' AND record_change_at<?""",
            (display_cutoff,),
        )
        counts = {
            "added": sum(1 for item in changes if item["change"] == "added"),
            "updated": sum(1 for item in changes if item["change"] in ("updated", "relisted")),
            "delisted": sum(1 for item in changes if item["change"] == "delisted"),
        }
        db.execute("INSERT INTO runs(mode,started_at,finished_at,status,total,added,updated,delisted) VALUES(?,?,?,?,?,?,?,?)",
                   ("initialize" if baseline else mode, started_at, now, "ok", len(current), counts["added"], counts["updated"], counts["delisted"]))
    return changes, counts


def list_rooms():
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM rooms ORDER BY status, updated_at DESC")]


def recent_display_changes():
    """返回仍处于 8 小时展示窗口的变化，供页面顶部和结果表使用同一语义。"""
    cutoff = (datetime.now(timezone.utc) - CHANGE_DISPLAY_RETENTION).isoformat().replace("+00:00", "Z")
    with connect() as db:
        rows = db.execute(
            """SELECT id,rental_type,url,record_change,record_change_at,status
               FROM rooms WHERE record_change!='未变化（复用）' AND record_change_at>=?
               ORDER BY record_change_at DESC,id DESC LIMIT 100""",
            (cutoff,),
        ).fetchall()
    return [{
        "id": row["id"], "rental_type": row["rental_type"], "url": row["url"],
        "change": row["record_change"], "recorded_at": row["record_change_at"],
        "status": "在架" if row["status"] == "active" else "已下架",
    } for row in rows]


def detail_target_ids(current, mode):
    with connect() as db:
        existing = {row["id"]: dict(row) for row in db.execute("SELECT id,summary,detail_json FROM rooms")}
    if mode == "full":
        return list(current)
    return [room_id for room_id, room in current.items() if room_id not in existing or not existing[room_id]["detail_json"] or existing[room_id]["summary"] != room.get("summary", "")]


def get_geocode_cache(address):
    with connect() as db:
        row = db.execute("SELECT result_json FROM geocode_cache WHERE address=?", (address,)).fetchone()
    return (True, json.loads(row[0])) if row else (False, None)


def put_geocode_cache(address, result):
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO geocode_cache(address,result_json,queried_at) VALUES(?,?,?)",
                   (address, json.dumps(result, ensure_ascii=False), _now()))


def save_room_detail(room_id, parsed):
    detail_json = json.dumps(parsed.get("detail") or {}, ensure_ascii=False)
    with connect() as db:
        db.execute("""UPDATE rooms SET detail_json=?,address=?,room_type_text=?,rental_type=?,updated_at=?
                      WHERE id=?""", (
            detail_json, parsed.get("address", ""), parsed.get("room_type_text", ""),
            parsed.get("rental_type") or "unknown", _now(), room_id,
        ))


def rooms_pending_geocode():
    with connect() as db:
        return [dict(row) for row in db.execute(
            """SELECT rooms.id,rooms.address FROM rooms
               LEFT JOIN geocode_cache ON geocode_cache.address=rooms.address
               WHERE rooms.address!='' AND (rooms.latitude IS NULL OR rooms.longitude IS NULL)
                 AND (geocode_cache.address IS NULL OR geocode_cache.result_json!='null')
               ORDER BY rooms.updated_at DESC"""
        )]


def save_room_coordinates(room_id, result):
    with connect() as db:
        if result:
            db.execute("""UPDATE rooms SET latitude=?,longitude=?,geocode_result=?,coordinate_accuracy=? WHERE id=?""",
                       (result["lat"], result["lon"], result.get("display_name", ""), result.get("accuracy", ""), room_id))
        else:
            db.execute("UPDATE rooms SET coordinate_accuracy='未定位' WHERE id=?", (room_id,))


def notification_rooms(room_ids):
    ids = [str(value) for value in room_ids if value]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with connect() as db:
        rows = db.execute(f"SELECT * FROM rooms WHERE id IN ({placeholders})", ids).fetchall()
    result = {}
    for row in rows:
        item = dict(row)
        try:
            item["detail"] = json.loads(item.get("detail_json") or "{}")
        except json.JSONDecodeError:
            item["detail"] = {}
        result[item["id"]] = item
    return result


def latest_change_batch():
    """返回最近一次产生实际变化的搜索批次，不受 8 小时页面展示状态影响。"""
    with connect() as db:
        latest = db.execute("SELECT happened_at FROM changes ORDER BY happened_at DESC LIMIT 1").fetchone()
        if not latest:
            return []
        rows = db.execute(
            "SELECT room_id,change_type,rental_type,happened_at,snapshot_json FROM changes WHERE happened_at=? ORDER BY id",
            (latest["happened_at"],),
        ).fetchall()
    changes = []
    for row in rows:
        try:
            item = json.loads(row["snapshot_json"] or "{}")
        except json.JSONDecodeError:
            item = {}
        item.update({
            "id": str(item.get("id") or row["room_id"]),
            "change": item.get("change") or row["change_type"],
            "rental_type": item.get("rental_type") or row["rental_type"],
            "recorded_at": item.get("recorded_at") or row["happened_at"],
        })
        changes.append(item)
    return changes


def latest_matching_change_batch(added_types, delisted_types):
    """从新到旧查找最近一批按当前设置确实需要通知的变化。"""
    with connect() as db:
        rows = db.execute(
            "SELECT room_id,change_type,rental_type,happened_at,snapshot_json FROM changes ORDER BY happened_at DESC,id DESC"
        ).fetchall()
    batches = {}
    order = []
    for row in rows:
        happened_at = row["happened_at"]
        if happened_at not in batches:
            batches[happened_at] = []
            order.append(happened_at)
        try:
            item = json.loads(row["snapshot_json"] or "{}")
        except json.JSONDecodeError:
            item = {}
        item.update({
            "id": str(item.get("id") or row["room_id"]),
            "change": item.get("change") or row["change_type"],
            "rental_type": item.get("rental_type") or row["rental_type"],
            "recorded_at": item.get("recorded_at") or happened_at,
        })
        batches[happened_at].append(item)
    for happened_at in order:
        selected = []
        for item in batches[happened_at]:
            if item["change"] in {"added", "relisted", "updated"} and item["rental_type"] in added_types:
                selected.append(item)
            elif item["change"] == "delisted" and item["rental_type"] in delisted_types:
                selected.append(item)
        if selected:
            return selected
    return []
