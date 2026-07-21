import os
import re
import hashlib
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for

from modules.admin.token_store import record_token_exchange, verify_authorization_header


bp = Blueprint("situation", __name__)

BASE_DIR = "/home/bbdwz/projects/website/data/situation"
LOG_PATH = os.path.join(BASE_DIR, "situation_log.txt")
SITE_PATH = os.path.join(BASE_DIR, "site.txt")


def _one_line(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _first_present(payload, names):
    for name in names:
        if name in payload:
            return payload.get(name)
    return ""


def _bool_text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return _one_line(value)


def _site_text(value):
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return f"{_one_line(value[0])},{_one_line(value[1])}"
    if isinstance(value, dict):
        lat = value.get("lat")
        lng = value.get("lng", value.get("lon"))
        if lat is not None and lng is not None:
            return f"{_one_line(lat)},{_one_line(lng)}"
    return _one_line(value)


def record_situation_event(payload):
    raw_time = _one_line(payload.get("time"))
    event_type = _one_line(_first_present(payload, ["event", "status", "statue"]))
    net = _one_line(_first_present(payload, ["net", "value"]))
    site = _site_text(payload.get("site"))
    battery = _one_line(payload.get("battery"))
    power = _bool_text(payload.get("power"))
    step = _one_line(payload.get("step"))
    event_id = _one_line(payload.get("id"))

    event = {
        "time": raw_time,
        "event": event_type,
        "net": net,
        "site": site,
        "battery": battery,
        "power": power,
        "step": step,
        "id": event_id
    }

    os.makedirs(BASE_DIR, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(
            f"{event['time']}\t{event['event']}\t{event['net']}\t{event['site']}"
            f"\t{event['battery']}\t{event['power']}\t{event['step']}\t{event['id']}\n"
        )
        f.flush()
        os.fsync(f.fileno())

    return event, None


def _parse_log_line(line):
    line = line.rstrip("\r\n")
    if not line:
        return None
    parts = line.split("\t")
    if len(parts) < 2:
        return None
    if len(parts) >= 4:
        if len(parts) == 7:
            battery = parts[4]
            power = ""
            step = parts[5]
            event_id = parts[6]
        else:
            battery = parts[4] if len(parts) > 4 else ""
            power = parts[5] if len(parts) > 5 else ""
            step = parts[6] if len(parts) > 6 else ""
            event_id = parts[7] if len(parts) > 7 else ""
        return {
            "time": parts[0],
            "event": parts[1],
            "net": parts[2],
            "site": parts[3],
            "battery": battery,
            "power": power,
            "step": step,
            "id": event_id
        }
    return {
        "time": parts[0],
        "event": "",
        "net": parts[1],
        "site": parts[2] if len(parts) > 2 else "",
        "battery": "",
        "power": "",
        "step": "",
        "id": ""
    }


def load_situation_events(limit=100):
    if not os.path.exists(LOG_PATH):
        return []

    events = []
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            event = _parse_log_line(line)
            if event:
                events.append(event)

    if limit and limit > 0:
        return events[-limit:]
    return events


def _iter_log_lines_reverse(block_size=8192):
    if not os.path.exists(LOG_PATH):
        return

    with open(LOG_PATH, "rb") as f:
        f.seek(0, os.SEEK_END)
        position = f.tell()
        buffer = b""

        while position > 0:
            read_size = min(block_size, position)
            position -= read_size
            f.seek(position)
            buffer = f.read(read_size) + buffer
            lines = buffer.split(b"\n")
            buffer = lines[0]

            for raw_line in reversed(lines[1:]):
                if raw_line:
                    yield raw_line.decode("utf-8", errors="replace")

        if buffer:
            yield buffer.decode("utf-8", errors="replace")


def load_recent_situation_events(days=7, limit=500):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    events = []

    for line in _iter_log_lines_reverse():
        event = _parse_log_line(line)
        if not event:
            continue

        event_time = _parse_event_time(event.get("time"))
        if event_time is not None and event_time.astimezone(timezone.utc) < cutoff:
            break

        events.append(event)
        if limit and len(events) >= limit:
            break

    return events


def _parse_event_time(value):
    match = re.match(
        r"^(\d{4})/(\d{1,2})/(\d{1,2})\s+GMT([+-]\d{1,2})(?::?(\d{2}))?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$",
        str(value or "").strip(),
        re.IGNORECASE
    )
    if not match:
        return None

    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))
    offset_hours = int(match.group(4))
    offset_minutes = int(match.group(5) or 0)
    hour = int(match.group(6))
    minute = int(match.group(7))
    second = int(match.group(8) or 0)
    offset_sign = 1 if offset_hours >= 0 else -1
    offset = timezone(timedelta(hours=offset_hours, minutes=offset_sign * offset_minutes))
    return datetime(year, month, day, hour, minute, second, tzinfo=offset)


BEIJING_TZ = timezone(timedelta(hours=8))


def _parse_client_time(value):
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=BEIJING_TZ)
        except ValueError:
            continue
    return _parse_event_time(text)


def _format_client_time(value):
    if not value:
        return ""
    return value.astimezone(BEIJING_TZ).strftime("%Y-%m-%dT%H:%M")


def _parse_site_point(value):
    parts = str(value or "").split(",", 1)
    if len(parts) != 2:
        return None
    try:
        lat = float(parts[0].strip())
        lng = float(parts[1].strip())
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def _latest_event_time():
    for line in _iter_log_lines_reverse():
        event = _parse_log_line(line)
        if not event:
            continue
        event_time = _parse_event_time(event.get("time"))
        if event_time:
            return event_time
    return datetime.now(BEIJING_TZ)


def load_track_points(start_time, end_time, limit=5000):
    points = []
    start_utc = start_time.astimezone(timezone.utc)
    end_utc = end_time.astimezone(timezone.utc)

    for line in _iter_log_lines_reverse():
        event = _parse_log_line(line)
        if not event:
            continue
        event_time = _parse_event_time(event.get("time"))
        if not event_time:
            continue

        event_utc = event_time.astimezone(timezone.utc)
        if event_utc < start_utc:
            break
        if event_utc > end_utc:
            continue

        point = _parse_site_point(event.get("site"))
        if not point:
            continue
        lat, lng = point
        points.append({
            "lat": lat,
            "lng": lng,
            "time": event.get("time", ""),
            "event": event.get("event", ""),
            "net": event.get("net", ""),
            "battery": event.get("battery", ""),
            "power": event.get("power", ""),
            "step": event.get("step", "")
        })
        if limit and len(points) >= limit:
            break

    return list(reversed(points))


def _page_unlocked():
    return session.get("situation_password_hash") == _password_hash(_get_page_password())


def _password_hash(password):
    return hashlib.sha256(str(password or "").encode("utf-8")).hexdigest()


def _load_site_config():
    config = {}
    current_key = None
    if not os.path.exists(SITE_PATH):
        return config

    with open(SITE_PATH, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            if line.endswith(":"):
                current_key = line[:-1].strip()
                config.setdefault(current_key, [])
                continue
            if current_key:
                config.setdefault(current_key, []).append(line)

    return config


def _get_page_password():
    env_password = os.environ.get("SITUATION_PASSWORD")
    if env_password:
        return env_password
    passwords = _load_site_config().get("password") or []
    if passwords:
        return passwords[0]
    return "situation"


def _unauthorized_api():
    return jsonify({"success": False, "status": "error", "error": "未授权"}), 401


def _situation_read_authorized():
    if session.get("logged_in"):
        g.situation_token_record = None
        return True
    if _page_unlocked():
        g.situation_token_record = None
        return True
    token_record = verify_authorization_header(request.headers.get("Authorization"), required_scope="situation:read")
    g.situation_token_record = token_record
    return token_record is not None


def _request_snapshot():
    return {
        "method": request.method,
        "path": request.path,
        "args": request.args.to_dict(flat=True),
        "json": request.get_json(silent=True),
    }


def _token_json_response(payload, status_code=200):
    token_record = getattr(g, "situation_token_record", None)
    if token_record:
        record_token_exchange(token_record.get("id"), _request_snapshot(), payload)
    return jsonify(payload), status_code


@bp.route("/situation", methods=["GET", "POST"])
def situation_page():
    error = ""
    if request.method == "POST":
        password = request.form.get("password") or ""
        if password == _get_page_password():
            session["situation_password_hash"] = _password_hash(password)
            return redirect(url_for("situation.situation_page"))
        error = "密码错误"

    return render_template(
        "situation.html",
        unlocked=_page_unlocked(),
        error=error
    )


@bp.route("/situation/map")
def situation_map_page():
    if not _page_unlocked():
        return redirect(url_for("situation.situation_page"))
    return render_template("situation_map.html")


@bp.route("/api/situation", methods=["POST"])
def api_record_situation():
    payload = request.get_json(silent=True) or {}
    event, error = record_situation_event(payload)
    if error:
        message, status_code = error
        return jsonify({
            "success": False,
            "error": message,
            "code": "SITUATION_RECORD_FAILED"
        }), status_code
    message = f"✅ 已记录状态：{event['event']} / {event['net']} @ {event['time']}"
    return jsonify({
        "success": True,
        "message": message,
        "data": event
    })


@bp.route("/api/situation/latest")
def api_latest_situation():
    if not _situation_read_authorized():
        return _unauthorized_api()
    events = load_situation_events(limit=1)
    latest = events[-1] if events else None
    return _token_json_response({"success": True, "status": "ok", "latest": latest})


@bp.route("/api/situation/settings")
def api_situation_settings():
    if not _situation_read_authorized():
        return _unauthorized_api()
    config = _load_site_config()
    return _token_json_response({
        "success": True,
        "status": "ok",
        "wifi": config.get("wifi") or []
    })


@bp.route("/api/situation/track")
def api_situation_track():
    if not _situation_read_authorized():
        return _unauthorized_api()

    end_time = _parse_client_time(request.args.get("end")) or _latest_event_time()
    start_time = _parse_client_time(request.args.get("start")) or (end_time - timedelta(hours=24))
    if start_time > end_time:
        start_time, end_time = end_time, start_time

    points = load_track_points(start_time, end_time)
    return _token_json_response({
        "success": True,
        "status": "ok",
        "start": _format_client_time(start_time),
        "end": _format_client_time(end_time),
        "points": points
    })


@bp.route("/api/situation/list")
def api_list_situations():
    if not _situation_read_authorized():
        return _unauthorized_api()
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100
    limit = max(1, min(limit, 500))
    try:
        days = int(request.args.get("days", 7))
    except ValueError:
        days = 7
    days = max(1, min(days, 3650))
    events = load_recent_situation_events(days=days, limit=limit)
    return _token_json_response({"success": True, "status": "ok", "events": events})
