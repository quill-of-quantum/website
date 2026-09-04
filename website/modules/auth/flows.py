import hashlib
import json
import os
import secrets
import threading
import time

from werkzeug.security import check_password_hash, generate_password_hash


FLOW_STORE_PATH = "/home/bbdwz/projects/website/data/admin/auth_flows.json"
REGISTRATION_TTL_SECONDS = 10 * 60
RESET_TTL_SECONDS = 30 * 60
SEND_COOLDOWN_SECONDS = 60
MAX_CODE_ATTEMPTS = 5
_LOCK = threading.Lock()


def _empty_store():
    return {"version": 1, "registrations": {}, "resets": {}, "rate_limits": {}}


def _load_unlocked():
    if not os.path.exists(FLOW_STORE_PATH):
        return _empty_store()
    try:
        with open(FLOW_STORE_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return _empty_store()
    except Exception:
        return _empty_store()
    data.setdefault("version", 1)
    for key in ("registrations", "resets", "rate_limits"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    return data


def _save_unlocked(data):
    os.makedirs(os.path.dirname(FLOW_STORE_PATH), exist_ok=True)
    temporary_path = FLOW_STORE_PATH + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=True, indent=2)
    os.replace(temporary_path, FLOW_STORE_PATH)


def _hash_token(value):
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _rate_key(scope, value):
    return _hash_token(f"{scope}:{value}")


def _prune_unlocked(data, now):
    data["registrations"] = {
        key: record for key, record in data["registrations"].items()
        if int(record.get("expires_at") or 0) > now
    }
    data["resets"] = {
        key: record for key, record in data["resets"].items()
        if int(record.get("expires_at") or 0) > now
    }
    data["rate_limits"] = {
        key: timestamp for key, timestamp in data["rate_limits"].items()
        if int(timestamp or 0) > now - 24 * 60 * 60
    }


def _reserve_send_unlocked(data, scope, identifiers, now):
    keys = [_rate_key(scope, value) for value in identifiers if value]
    if any(now - int(data["rate_limits"].get(key) or 0) < SEND_COOLDOWN_SECONDS for key in keys):
        return False
    for key in keys:
        data["rate_limits"][key] = now
    return True


def create_registration(username, email, password_hash, client_id="", now=None):
    now = int(time.time() if now is None else now)
    code = f"{secrets.randbelow(1_000_000):06d}"
    flow_id = secrets.token_urlsafe(24)
    with _LOCK:
        data = _load_unlocked()
        _prune_unlocked(data, now)
        if not _reserve_send_unlocked(data, "register", (email, client_id), now):
            _save_unlocked(data)
            return None, None, "rate-limited"
        data["registrations"][flow_id] = {
            "username": username,
            "email": email,
            "password_hash": password_hash,
            "code_hash": generate_password_hash(code),
            "attempts": 0,
            "created_at": now,
            "expires_at": now + REGISTRATION_TTL_SECONDS,
        }
        _save_unlocked(data)
    return flow_id, code, None


def cancel_registration(flow_id):
    with _LOCK:
        data = _load_unlocked()
        changed = data["registrations"].pop(str(flow_id or ""), None) is not None
        if changed:
            _save_unlocked(data)
        return changed


def verify_registration_code(flow_id, code, now=None):
    now = int(time.time() if now is None else now)
    with _LOCK:
        data = _load_unlocked()
        _prune_unlocked(data, now)
        record = data["registrations"].get(str(flow_id or ""))
        if not record:
            _save_unlocked(data)
            return None, "invalid-or-expired"
        if not check_password_hash(record.get("code_hash") or "", str(code or "")):
            record["attempts"] = int(record.get("attempts") or 0) + 1
            if record["attempts"] >= MAX_CODE_ATTEMPTS:
                data["registrations"].pop(str(flow_id), None)
                error = "too-many-attempts"
            else:
                error = "invalid-code"
            _save_unlocked(data)
            return None, error
        return dict(record), None


def consume_registration(flow_id):
    return cancel_registration(flow_id)


def create_password_reset(username, email, client_id="", now=None):
    now = int(time.time() if now is None else now)
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    with _LOCK:
        data = _load_unlocked()
        _prune_unlocked(data, now)
        if not _reserve_send_unlocked(data, "reset", (email, client_id), now):
            _save_unlocked(data)
            return None, "rate-limited"
        data["resets"] = {
            key: record for key, record in data["resets"].items()
            if record.get("username") != username
        }
        data["resets"][token_hash] = {
            "username": username,
            "email": email,
            "created_at": now,
            "expires_at": now + RESET_TTL_SECONDS,
        }
        _save_unlocked(data)
    return token, None


def get_password_reset(token, now=None):
    now = int(time.time() if now is None else now)
    with _LOCK:
        data = _load_unlocked()
        _prune_unlocked(data, now)
        record = data["resets"].get(_hash_token(token))
        _save_unlocked(data)
        return dict(record) if record else None


def consume_password_reset(token):
    with _LOCK:
        data = _load_unlocked()
        changed = data["resets"].pop(_hash_token(token), None) is not None
        if changed:
            _save_unlocked(data)
        return changed


def take_password_reset(token, now=None):
    now = int(time.time() if now is None else now)
    with _LOCK:
        data = _load_unlocked()
        _prune_unlocked(data, now)
        record = data["resets"].pop(_hash_token(token), None)
        _save_unlocked(data)
        return dict(record) if record else None
