import hashlib
import json
import os
import threading
import time
import uuid

from werkzeug.security import check_password_hash, generate_password_hash


USER_STORE_PATH = "/home/bbdwz/projects/website/data/admin/users.json"
DEFAULT_ADMIN_PASSWORD = "bbdwz"
ROLE_GUEST = "guest"
ROLE_MEMBER = "member"
ROLE_ADMIN = "admin"
ASSIGNABLE_ROLES = {ROLE_GUEST, ROLE_MEMBER}
ROLE_PERMISSIONS = {
    ROLE_GUEST: set(),
    ROLE_MEMBER: {"situation:view", "cloud:delete"},
    ROLE_ADMIN: {"situation:view", "cloud:delete", "system:view"},
}
_LOCK = threading.RLock()


def normalize_role(role):
    role = str(role or "").strip().lower()
    return role if role in ROLE_PERMISSIONS else ROLE_GUEST


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
        return {}
    try:
        with open(USER_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("invalid users store")
    except Exception:
        return {}
    return data


def _save_users(users):
    os.makedirs(os.path.dirname(USER_STORE_PATH), exist_ok=True)
    tmp_path = USER_STORE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=True, indent=2)
    os.replace(tmp_path, USER_STORE_PATH)


def normalize_email(email):
    return str(email or "").strip().lower()


def valid_username(username):
    return (
        3 <= len(username) <= 32
        and username.lower() != "admin"
        and username.replace("_", "").replace("-", "").replace(".", "").isalnum()
    )


def list_users():
    with _LOCK:
        users = _load_users()
        result = []
        for username, record in sorted(users.items()):
            result.append({
                "username": username,
                "email": normalize_email(record.get("email")),
                "role": normalize_role(record.get("role")),
                "permissions": sorted(ROLE_PERMISSIONS[normalize_role(record.get("role"))]),
                "created_at": record.get("created_at"),
            })
        return result


def verify_user_password(username, password):
    with _LOCK:
        users = _load_users()
        record = users.get(str(username or ""))
        if not record:
            return False
        return check_password_hash(record.get("password_hash") or "", str(password or ""))


def get_user_role(username):
    with _LOCK:
        users = _load_users()
        record = users.get(str(username or ""))
        if not record:
            return ""
        return normalize_role(record.get("role"))


def get_user_permissions(username):
    return sorted(ROLE_PERMISSIONS.get(get_user_role(username), set()))


def get_user_id(username):
    username = str(username or "")
    with _LOCK:
        users = _load_users()
        record = users.get(username)
        if not record:
            return ""
        account_id = str(record.get("id") or "").strip()
        if not account_id:
            # Keep pre-ID accounts compatible with their existing chat identity.
            account_id = hashlib.sha256(username.encode("utf-8")).hexdigest()
            record["id"] = account_id
            _save_users(users)
        return account_id


def user_has_permission(username, permission):
    return str(permission or "") in ROLE_PERMISSIONS.get(get_user_role(username), set())


def user_exists(username):
    with _LOCK:
        users = _load_users()
        return str(username or "") in users


def get_username_by_email(email):
    email = normalize_email(email)
    if not email:
        return None
    with _LOCK:
        for username, record in _load_users().items():
            if normalize_email(record.get("email")) == email:
                return username
    return None


def get_user_email(username):
    with _LOCK:
        record = _load_users().get(str(username or ""))
        return normalize_email(record.get("email")) if record else ""


def email_exists(email):
    return get_username_by_email(email) is not None


def is_admin_user(username):
    return str(username or "") == "admin" and get_user_role(username) == ROLE_ADMIN


def create_user(username, password, role=ROLE_GUEST, email=None):
    username = str(username or "").strip()
    password = str(password or "")
    role = str(role or ROLE_GUEST).strip().lower()
    if not valid_username(username):
        return False, "invalid-username"
    if not password:
        return False, "missing-password"
    return create_user_from_password_hash(username, generate_password_hash(password), role, email=email)


def create_user_from_password_hash(username, password_hash, role=ROLE_GUEST, email=None):
    username = str(username or "").strip()
    password_hash = str(password_hash or "")
    role = str(role or ROLE_GUEST).strip().lower()
    email = normalize_email(email)
    if not valid_username(username):
        return False, "invalid-username"
    if not password_hash:
        return False, "missing-password"
    if role not in ASSIGNABLE_ROLES:
        return False, "invalid-role"
    with _LOCK:
        users = _load_users()
        if username in users:
            return False, "user-exists"
        if email and any(normalize_email(record.get("email")) == email for record in users.values()):
            return False, "email-exists"
        users[username] = {
            "id": uuid.uuid4().hex,
            "password_hash": password_hash,
            "email": email,
            "role": role,
            "created_at": int(time.time()),
        }
        _save_users(users)
        return True, None


def update_user_role(username, role):
    username = str(username or "").strip()
    role = str(role or "").strip().lower()
    if username == "admin":
        return False, "cannot-change-admin-role"
    if role not in ASSIGNABLE_ROLES:
        return False, "invalid-role"
    with _LOCK:
        users = _load_users()
        if username not in users:
            return False, "user-not-found"
        users[username]["role"] = role
        _save_users(users)
        return True, None


def update_user_password(username, password):
    username = str(username or "").strip()
    password = str(password or "")
    if not password:
        return False, "missing-password"
    password_hash = generate_password_hash(password)
    with _LOCK:
        users = _load_users()
        if username not in users:
            return False, "user-not-found"
        users[username]["password_hash"] = password_hash
        _save_users(users)
        return True, None


def update_username(username, new_username, current_password):
    username = str(username or "").strip()
    new_username = str(new_username or "").strip()
    if username == "admin":
        return False, "cannot-change-admin"
    if not valid_username(new_username):
        return False, "invalid-username"
    with _LOCK:
        users = _load_users()
        record = users.get(username)
        if not record:
            return False, "user-not-found"
        if not check_password_hash(record.get("password_hash") or "", str(current_password or "")):
            return False, "invalid-password"
        if new_username != username and new_username in users:
            return False, "user-exists"
        if new_username == username:
            return True, None
        if not record.get("id"):
            record["id"] = hashlib.sha256(username.encode("utf-8")).hexdigest()
        users[new_username] = record
        users.pop(username)
        _save_users(users)
        return True, None


def update_password_with_current(username, current_password, new_password):
    username = str(username or "").strip()
    new_password = str(new_password or "")
    if username == "admin":
        return False, "cannot-change-admin"
    if not new_password:
        return False, "missing-password"
    with _LOCK:
        users = _load_users()
        record = users.get(username)
        if not record:
            return False, "user-not-found"
        if not check_password_hash(record.get("password_hash") or "", str(current_password or "")):
            return False, "invalid-password"
        record["password_hash"] = generate_password_hash(new_password)
        _save_users(users)
        return True, None


def delete_user(username):
    username = str(username or "").strip()
    if username == "admin":
        return False, "cannot-delete-admin"
    with _LOCK:
        users = _load_users()
        if username not in users:
            return False, "user-not-found"
        users.pop(username, None)
        _save_users(users)
        return True, None
