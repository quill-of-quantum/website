import json
import os
import time

from werkzeug.security import check_password_hash, generate_password_hash


USER_STORE_PATH = "/home/bbdwz/projects/website/data/admin/users.json"
DEFAULT_ADMIN_PASSWORD = "bbdwz"


def _default_users():
    return {
        "admin": {
            "password_hash": generate_password_hash(DEFAULT_ADMIN_PASSWORD),
            "role": "admin",
            "created_at": int(time.time()),
        }
    }


def _load_users():
    if not os.path.exists(USER_STORE_PATH):
        users = _default_users()
        _save_users(users)
        return users
    try:
        with open(USER_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("invalid users store")
    except Exception:
        data = _default_users()
    admin = data.get("admin")
    if not isinstance(admin, dict) or admin.get("role") != "admin":
        data["admin"] = _default_users()["admin"]
        _save_users(data)
    return data


def _save_users(users):
    os.makedirs(os.path.dirname(USER_STORE_PATH), exist_ok=True)
    tmp_path = USER_STORE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=True, indent=2)
    os.replace(tmp_path, USER_STORE_PATH)


def list_users():
    users = _load_users()
    result = []
    for username, record in sorted(users.items()):
        result.append({
            "username": username,
            "role": record.get("role") or "user",
            "created_at": record.get("created_at"),
        })
    return result


def verify_user_password(username, password):
    users = _load_users()
    record = users.get(str(username or ""))
    if not record:
        return False
    return check_password_hash(record.get("password_hash") or "", str(password or ""))


def get_user_role(username):
    users = _load_users()
    record = users.get(str(username or ""))
    return (record or {}).get("role") or ""


def user_exists(username):
    users = _load_users()
    return str(username or "") in users


def is_admin_user(username):
    return str(username or "") == "admin" and get_user_role(username) == "admin"


def create_user(username, password, role="user"):
    username = str(username or "").strip()
    password = str(password or "")
    role = "user"
    if not username or not username.replace("_", "").replace("-", "").replace(".", "").isalnum():
        return False, "invalid-username"
    if username == "admin":
        return False, "admin-reserved"
    if not password:
        return False, "missing-password"
    users = _load_users()
    if username in users:
        return False, "user-exists"
    users[username] = {
        "password_hash": generate_password_hash(password),
        "role": role,
        "created_at": int(time.time()),
    }
    _save_users(users)
    return True, None


def update_user_password(username, password):
    username = str(username or "").strip()
    password = str(password or "")
    if not password:
        return False, "missing-password"
    users = _load_users()
    if username not in users:
        return False, "user-not-found"
    users[username]["password_hash"] = generate_password_hash(password)
    _save_users(users)
    return True, None


def delete_user(username):
    username = str(username or "").strip()
    if username == "admin":
        return False, "cannot-delete-admin"
    users = _load_users()
    if username not in users:
        return False, "user-not-found"
    users.pop(username, None)
    _save_users(users)
    return True, None
