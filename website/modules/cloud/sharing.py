import hashlib
import json
import os
import secrets
import time
import uuid

SHARE_STORE_PATH = "/home/bbdwz/projects/website/storage/cloud/shares.json"
MIN_SHARE_SECONDS = 5 * 60
MAX_SHARE_SECONDS = 30 * 24 * 60 * 60

class ShareTokenError(ValueError):
    pass

def normalize_share_seconds(value):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = 7 * 24 * 60 * 60
    return max(MIN_SHARE_SECONDS, min(seconds, MAX_SHARE_SECONDS))

def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def _load_records():
    try:
        with open(SHARE_STORE_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def _save_records(records):
    os.makedirs(os.path.dirname(SHARE_STORE_PATH), exist_ok=True)
    temporary = f"{SHARE_STORE_PATH}.tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(records, file, ensure_ascii=False)
    os.replace(temporary, SHARE_STORE_PATH)

def create_share(files, expires_in, now=None):
    now = int(time.time() if now is None else now)
    token = secrets.token_urlsafe(24)
    record = {"id": uuid.uuid4().hex, "token": token, "token_hash": _token_hash(token), "files": files,
              "created_at": now, "expires_at": now + normalize_share_seconds(expires_in), "revoked": False}
    records = _load_records()
    records = [item for item in records if int(item.get("expires_at", 0)) > now - 30 * 24 * 60 * 60][-999:]
    records.append(record)
    _save_records(records)
    return token, record

def resolve_share(token, now=None):
    now = int(time.time() if now is None else now)
    digest = _token_hash(token)
    record = next((item for item in _load_records() if item.get("token_hash") == digest), None)
    if not record or record.get("revoked"):
        raise ShareTokenError("分享链接无效或已撤销")
    if int(record.get("expires_at", 0)) <= now:
        raise ShareTokenError("分享链接已过期")
    return record

def list_shares(now=None):
    now = int(time.time() if now is None else now)
    return [{"id": r.get("id"), "token": r.get("token"), "files": r.get("files", []), "created_at": r.get("created_at"),
             "expires_at": r.get("expires_at"),
             "active": not r.get("revoked") and int(r.get("expires_at", 0)) > now}
            for r in reversed(_load_records())]

def revoke_share(share_id):
    records = _load_records()
    for record in records:
        if record.get("id") == share_id:
            record["revoked"] = True
            _save_records(records)
            return True
    return False
