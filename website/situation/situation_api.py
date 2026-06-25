import os
import re
import hashlib

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for


bp = Blueprint("situation", __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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


def record_situation_event(payload):
    raw_time = _one_line(payload.get("time"))
    event_type = _one_line(_first_present(payload, ["event", "status", "statue"]))
    net = _one_line(_first_present(payload, ["net", "value"]))
    site = _one_line(payload.get("site"))
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


def load_situation_events(limit=100):
    if not os.path.exists(LOG_PATH):
        return []

    events = []
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
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
                events.append({
                    "time": parts[0],
                    "event": parts[1],
                    "net": parts[2],
                    "site": parts[3],
                    "battery": battery,
                    "power": power,
                    "step": step,
                    "id": event_id
                })
            else:
                events.append({
                    "time": parts[0],
                    "event": "",
                    "net": parts[1],
                    "site": parts[2] if len(parts) > 2 else "",
                    "battery": "",
                    "power": "",
                    "step": "",
                    "id": ""
                })

    if limit and limit > 0:
        return events[-limit:]
    return events


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
    return jsonify({"status": "error", "error": "未授权"}), 401


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


@bp.route("/api/situation", methods=["POST"])
def api_record_situation():
    payload = request.get_json(silent=True) or {}
    event, error = record_situation_event(payload)
    if error:
        message, status_code = error
        return jsonify({"status": "error", "error": message}), status_code
    return jsonify({
        "status": "ok",
        "event": event,
        "reply": f"✅ 已记录状态：{event['event']} / {event['net']} @ {event['time']}"
    })


@bp.route("/api/situation/latest")
def api_latest_situation():
    if not _page_unlocked():
        return _unauthorized_api()
    events = load_situation_events(limit=1)
    latest = events[-1] if events else None
    return jsonify({"status": "ok", "latest": latest})


@bp.route("/api/situation/settings")
def api_situation_settings():
    if not _page_unlocked():
        return _unauthorized_api()
    config = _load_site_config()
    return jsonify({
        "status": "ok",
        "wifi": config.get("wifi") or []
    })


@bp.route("/api/situation/list")
def api_list_situations():
    if not _page_unlocked():
        return _unauthorized_api()
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100
    limit = max(1, min(limit, 500))
    events = load_situation_events(limit=limit)
    return jsonify({"status": "ok", "events": list(reversed(events))})
