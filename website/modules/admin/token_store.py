import hashlib
import json
import os
import secrets
import threading
import uuid
from datetime import datetime, timezone


TOKEN_STORE_PATH = "/home/bbdwz/projects/website/data/admin/app_tokens.json"
DEFAULT_SCOPES = ["situation:read"]
MAX_JSON_TEXT_LEN = 4000
_LOCK = threading.Lock()


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash_token(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _load_store_unlocked():
    if not os.path.exists(TOKEN_STORE_PATH):
        return {"version": 1, "tokens": []}
    try:
        with open(TOKEN_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": 1, "tokens": []}
        tokens = data.get("tokens")
        if not isinstance(tokens, list):
            data["tokens"] = []
        data.setdefault("version", 1)
        return data
    except Exception:
        return {"version": 1, "tokens": []}


def _save_store_unlocked(data):
    os.makedirs(os.path.dirname(TOKEN_STORE_PATH), exist_ok=True)
    tmp_path = TOKEN_STORE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=True, indent=2)
    os.replace(tmp_path, TOKEN_STORE_PATH)


def _public_token(record):
    return {
        "id": record.get("id", ""),
        "label": record.get("label", ""),
        "owner": record.get("owner", ""),
        "scopes": record.get("scopes", []),
        "created_at": record.get("created_at", ""),
        "last_used_at": record.get("last_used_at", ""),
        "revoked_at": record.get("revoked_at", ""),
        "token_prefix": record.get("token_prefix", ""),
        "last_request_json": record.get("last_request_json", None),
        "last_response_json": record.get("last_response_json", None),
    }


def _compact_json(value):
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return {"truncated": True, "value": str(value)[:MAX_JSON_TEXT_LEN]}
    if len(text) <= MAX_JSON_TEXT_LEN:
        return value
    return {
        "truncated": True,
        "chars": len(text),
        "preview": text[:MAX_JSON_TEXT_LEN],
    }


def list_tokens():
    with _LOCK:
        data = _load_store_unlocked()
        return [_public_token(record) for record in data.get("tokens", [])]


def create_token(label, owner, scopes=None):
    label = str(label or "").strip()[:80] or "Android App"
    owner = str(owner or "").strip()[:80] or "unknown"
    scopes = scopes if isinstance(scopes, list) and scopes else DEFAULT_SCOPES
    scopes = [str(scope).strip() for scope in scopes if str(scope).strip()]
    if not scopes:
        scopes = DEFAULT_SCOPES

    raw_token = "wst_" + secrets.token_urlsafe(32)
    record = {
        "id": uuid.uuid4().hex,
        "label": label,
        "owner": owner,
        "scopes": scopes,
        "token_hash": _hash_token(raw_token),
        "token_prefix": raw_token[:12],
        "created_at": _now_iso(),
        "last_used_at": "",
        "revoked_at": "",
    }
    with _LOCK:
        data = _load_store_unlocked()
        data.setdefault("tokens", []).insert(0, record)
        _save_store_unlocked(data)
    return raw_token, _public_token(record)


def revoke_token(token_id):
    token_id = str(token_id or "").strip()
    if not token_id:
        return False
    with _LOCK:
        data = _load_store_unlocked()
        changed = False
        for record in data.get("tokens", []):
            if record.get("id") == token_id:
                if not record.get("revoked_at"):
                    record["revoked_at"] = _now_iso()
                changed = True
                break
        if changed:
            _save_store_unlocked(data)
        return changed


def enable_token(token_id):
    token_id = str(token_id or "").strip()
    if not token_id:
        return False
    with _LOCK:
        data = _load_store_unlocked()
        changed = False
        for record in data.get("tokens", []):
            if record.get("id") == token_id:
                record["revoked_at"] = ""
                changed = True
                break
        if changed:
            _save_store_unlocked(data)
        return changed


def delete_token(token_id):
    token_id = str(token_id or "").strip()
    if not token_id:
        return False
    with _LOCK:
        data = _load_store_unlocked()
        before = len(data.get("tokens", []))
        data["tokens"] = [record for record in data.get("tokens", []) if record.get("id") != token_id]
        changed = len(data["tokens"]) != before
        if changed:
            _save_store_unlocked(data)
        return changed


def verify_token(token, required_scope=None):
    token_hash = _hash_token(token)
    with _LOCK:
        data = _load_store_unlocked()
        for record in data.get("tokens", []):
            if record.get("token_hash") != token_hash:
                continue
            if record.get("revoked_at"):
                return None
            scopes = record.get("scopes") or []
            if required_scope and required_scope not in scopes:
                return None
            record["last_used_at"] = _now_iso()
            _save_store_unlocked(data)
            return _public_token(record)
    return None


def record_token_exchange(token_id, request_json, response_json):
    token_id = str(token_id or "").strip()
    if not token_id:
        return False
    with _LOCK:
        data = _load_store_unlocked()
        changed = False
        for record in data.get("tokens", []):
            if record.get("id") == token_id:
                record["last_request_json"] = _compact_json(request_json)
                record["last_response_json"] = _compact_json(response_json)
                record["last_used_at"] = _now_iso()
                changed = True
                break
        if changed:
            _save_store_unlocked(data)
        return changed


def verify_authorization_header(header, required_scope=None):
    value = str(header or "").strip()
    if not value.lower().startswith("bearer "):
        return None
    token = value.split(" ", 1)[1].strip()
    if not token:
        return None
    return verify_token(token, required_scope=required_scope)
