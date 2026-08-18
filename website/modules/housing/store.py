import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "housing"
CONFIG_PATH = DATA_DIR / "config.json"
STATE_PATH = DATA_DIR / "state.json"
RUN_REQUEST_PATH = DATA_DIR / "run_requested"
PID_PATH = DATA_DIR / "service.pid"
_LOCK = threading.Lock()

RENTAL_TYPES = ("wg", "einzelzimmer", "xzimmer")
DEFAULT_CONFIG = {
    "incremental_interval_minutes": 5,
    "full_interval_minutes": 1440,
    "email_enabled": False,
    "notification_emails": [],
    "notify_added_types": list(RENTAL_TYPES),
    "notify_delisted_types": list(RENTAL_TYPES),
    "username": "",
    "password": "",
}


def _read_json(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default)
    except (OSError, json.JSONDecodeError):
        return dict(default)


def _atomic_write(path, value, private=False):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600 if private else 0o644)
    os.replace(temporary, path)


def load_config(include_password=False):
    with _LOCK:
        saved = _read_json(CONFIG_PATH, DEFAULT_CONFIG)
        # 兼容第一版设置，读取后在下次保存时转换为新格式。
        if "incremental_interval_minutes" not in saved:
            saved["incremental_interval_minutes"] = saved.get("interval_minutes", 5)
        if "full_interval_minutes" not in saved:
            saved["full_interval_minutes"] = 1440
        if "notification_emails" not in saved:
            old_email = str(saved.get("notification_email") or "").strip()
            saved["notification_emails"] = [old_email] if old_email else []
        old_types = saved.get("rental_types") or list(RENTAL_TYPES)
        saved.setdefault("notify_added_types", old_types)
        saved.setdefault("notify_delisted_types", old_types)
        config = {**DEFAULT_CONFIG, **saved}
    config["password_configured"] = bool(config.get("password"))
    if not include_password:
        config.pop("password", None)
    return config


def validate_config(payload):
    current = load_config(include_password=True)
    try:
        incremental_interval = int(payload.get(
            "incremental_interval_minutes", current["incremental_interval_minutes"]
        ))
        full_interval = int(payload.get("full_interval_minutes", current["full_interval_minutes"]))
    except (TypeError, ValueError):
        raise ValueError("检查间隔必须是整数分钟")
    if not 1 <= incremental_interval <= 10080 or not 1 <= full_interval <= 10080:
        raise ValueError("检查间隔必须在 1 到 10080 分钟之间")

    def selected_types(key):
        values = payload.get(key, current[key])
        if not isinstance(values, list):
            raise ValueError("房型设置无效")
        return [item for item in RENTAL_TYPES if item in values]

    notify_added_types = selected_types("notify_added_types")
    notify_delisted_types = selected_types("notify_delisted_types")

    username = str(payload.get("username", current.get("username", ""))).strip()[:200]
    password = payload.get("password")
    if password is None or password == "":
        password = current.get("password", "")
    else:
        password = str(password)[:500]
    if payload.get("clear_password"):
        password = ""

    email_enabled = bool(payload.get("email_enabled", current["email_enabled"]))
    emails = payload.get("notification_emails", current.get("notification_emails", []))
    if isinstance(emails, str):
        emails = emails.replace(";", ",").replace("\n", ",").split(",")
    if not isinstance(emails, list):
        raise ValueError("通知邮箱格式无效")
    notification_emails = []
    for value in emails:
        email = str(value or "").strip()[:320]
        if email and email not in notification_emails:
            if "@" not in email:
                raise ValueError(f"邮箱格式无效：{email}")
            notification_emails.append(email)
    if email_enabled and not notification_emails:
        raise ValueError("打开邮件通知后必须至少填写一个收件地址")

    return {
        "incremental_interval_minutes": incremental_interval,
        "full_interval_minutes": full_interval,
        "email_enabled": email_enabled,
        "notification_emails": notification_emails,
        "notify_added_types": notify_added_types,
        "notify_delisted_types": notify_delisted_types,
        "username": username,
        "password": password,
    }


def save_config(payload):
    config = validate_config(payload)
    with _LOCK:
        _atomic_write(CONFIG_PATH, config, private=True)
    return load_config()


def load_state():
    with _LOCK:
        return _read_json(STATE_PATH, {
            "status": "never_run", "last_started_at": "", "last_finished_at": "",
            "last_error": "", "last_counts": {}, "last_changes": [], "rooms": {},
        })


def save_state(state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with _LOCK:
        _atomic_write(STATE_PATH, state)


def request_run(mode="incremental"):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUN_REQUEST_PATH.write_text(mode if mode in ("full", "incremental", "initialize") else "incremental", encoding="ascii")


def consume_run_request():
    try:
        mode = RUN_REQUEST_PATH.read_text(encoding="ascii").strip()
        RUN_REQUEST_PATH.unlink()
        return mode if mode in ("full", "incremental", "initialize") else "incremental"
    except FileNotFoundError:
        return None


def write_service_pid(pid):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(int(pid)), encoding="ascii")


def clear_service_pid(pid):
    try:
        if int(PID_PATH.read_text(encoding="ascii").strip()) == int(pid):
            PID_PATH.unlink()
    except (FileNotFoundError, OSError, ValueError):
        pass


def service_pid_alive():
    try:
        pid = int(PID_PATH.read_text(encoding="ascii").strip())
        os.kill(pid, 0)
        return True, pid
    except (FileNotFoundError, OSError, ValueError):
        return False, None
