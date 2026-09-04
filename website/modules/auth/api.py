import html
import os
import re
import time

import requests
from flask import Blueprint, jsonify, redirect, render_template, request, session
from werkzeug.security import generate_password_hash

from modules.cloud.endpoints import load_public_origins
from modules.auth.flows import (
    REGISTRATION_TTL_SECONDS,
    RESET_TTL_SECONDS,
    cancel_registration,
    consume_registration,
    create_password_reset,
    create_registration,
    get_password_reset,
    take_password_reset,
    verify_registration_code,
)
from modules.auth.user_store import (
    ROLE_GUEST,
    create_user_from_password_hash,
    email_exists,
    get_user_permissions,
    get_user_email,
    get_user_role,
    get_username_by_email,
    normalize_email,
    update_password_with_current,
    update_username,
    update_user_password,
    user_exists,
    valid_username,
    verify_user_password,
)


bp = Blueprint("auth", __name__)
EMAIL_SERVICE_URL = os.getenv("EMAIL_SERVICE_URL", "http://127.0.0.1:8081").rstrip("/")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PASSWORD_MIN_LENGTH = 8
GENERIC_RESET_MESSAGE = "如果该邮箱已注册，找回链接会在几分钟内发送。"


def _client_id():
    forwarded = str(request.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
    return forwarded or str(request.remote_addr or "unknown")


def _mask_email(email):
    local, separator, domain = normalize_email(email).partition("@")
    if not separator:
        return ""
    return f"{local[:2]}***@{domain}"


def _public_base_url():
    configured = str(os.getenv("AUTH_PUBLIC_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    primary, backups = load_public_origins()
    known_origins = [str(origin).strip().rstrip("/") for origin in [*primary, *backups] if str(origin).strip()]
    if known_origins:
        current_host = request.host.lower()
        for origin in known_origins:
            if origin.split("://", 1)[-1].lower() == current_host:
                return origin
        return known_origins[0]
    forwarded_scheme = str(request.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
    scheme = forwarded_scheme if forwarded_scheme in {"http", "https"} else request.scheme
    return f"{scheme}://{request.host}"


def _send_account_email(to, subject, text, email_html):
    payload = {"to": [to], "cc": [], "bcc": [], "subject": subject, "text": text, "html": email_html}
    try:
        response = requests.post(
            f"{EMAIL_SERVICE_URL}/api/mail/send/default",
            json=payload,
            timeout=30,
        )
        return response.status_code < 400
    except requests.RequestException:
        return False


def _email_frame(title, lead, content):
    return f"""<!doctype html>
<html lang="zh-CN"><body style="margin:0;background:#f4f6f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#172033">
<div style="padding:40px 16px"><div style="max-width:560px;margin:0 auto;background:#fff;border:1px solid #e5e9f0;border-radius:18px;overflow:hidden;box-shadow:0 12px 36px rgba(15,23,42,.08)">
<div style="height:5px;background:linear-gradient(90deg,#2563eb,#7c3aed)"></div>
<div style="padding:34px"><div style="font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#64748b;margin-bottom:12px">BBDWZ ACCOUNT</div>
<h1 style="font-size:25px;line-height:1.3;margin:0 0 12px">{title}</h1><p style="color:#64748b;line-height:1.7;margin:0 0 26px">{lead}</p>
{content}<p style="margin:28px 0 0;color:#94a3b8;font-size:12px;line-height:1.6">如果这不是你的操作，请忽略此邮件。请勿向任何人透露验证码或重置链接。</p>
</div></div></div></body></html>"""


def _registration_email(username, code):
    safe_username = html.escape(username)
    safe_code = html.escape(code)
    content = f"""<div style="padding:20px;border-radius:14px;background:#f8fafc;border:1px solid #e2e8f0;text-align:center">
<div style="font-size:13px;color:#64748b;margin-bottom:10px">{safe_username} 的验证码</div>
<div style="font-size:34px;font-weight:750;letter-spacing:.24em;color:#1d4ed8;padding-left:.24em">{safe_code}</div>
<div style="font-size:13px;color:#64748b;margin-top:10px">10 分钟内有效</div></div>"""
    return _email_frame("确认你的邮箱", "输入下面的验证码，即可完成游客账户注册。", content)


def _reset_email(username, reset_url):
    safe_username = html.escape(username)
    safe_url = html.escape(reset_url, quote=True)
    content = f"""<div style="padding:20px;border-radius:14px;background:#f8fafc;border:1px solid #e2e8f0">
<div style="font-size:13px;color:#64748b;margin-bottom:14px">账户名</div><div style="font-size:20px;font-weight:700;margin-bottom:22px">{safe_username}</div>
<a href="{safe_url}" style="display:inline-block;padding:12px 20px;border-radius:10px;background:#2563eb;color:#fff;text-decoration:none;font-weight:700">设置新密码</a>
<div style="font-size:13px;color:#64748b;margin-top:14px">链接 30 分钟内有效，使用一次后立即失效。</div></div>"""
    return _email_frame("找回账户与密码", "我们收到了这个邮箱的账户找回请求。", content)


def _password_error(password, confirmation):
    if password != confirmation:
        return "两次输入的密码不一致"
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"密码至少需要 {PASSWORD_MIN_LENGTH} 个字符"
    if len(password) > 256:
        return "密码过长"
    return None


def _auth_payload(username=None):
    logged_in = bool(username)
    role = get_user_role(username) if logged_in else None
    return {
        "logged_in": logged_in,
        "user": username if logged_in else None,
        "role": role,
        "permissions": get_user_permissions(username) if logged_in else [],
        "is_admin": role == "admin" if logged_in else False,
    }


@bp.route("/register")
def register_page():
    return render_template("auth_register.html")


@bp.route("/account")
def account_page():
    username = session.get("user") if session.get("logged_in") else None
    if not username or not user_exists(username):
        session.clear()
        return redirect("/")
    if get_user_role(username) == "admin":
        return redirect("/1/")
    return render_template(
        "auth_account.html",
        account_username=username,
        account_email=get_user_email(username),
        account_role=get_user_role(username),
    )


def _account_username():
    username = session.get("user") if session.get("logged_in") else None
    if not username or not user_exists(username):
        session.clear()
        return None
    return username


@bp.route("/api/auth/account/username", methods=["POST"])
def account_update_username():
    username = _account_username()
    if not username:
        return jsonify({"success": False, "error": "请先登录"}), 401
    if get_user_role(username) == "admin":
        return jsonify({"success": False, "error": "管理员请在后台管理账户"}), 403

    data = request.get_json(silent=True) or {}
    new_username = str(data.get("new_username") or "").strip()
    current_password = str(data.get("current_password") or "")
    ok, error = update_username(username, new_username, current_password)
    if not ok:
        messages = {
            "invalid-password": "当前密码不正确",
            "invalid-username": "用户名需为 3–32 位字母、数字、中文、点、横线或下划线",
            "user-exists": "用户名已被使用",
            "user-not-found": "账户不存在",
        }
        status = 409 if error == "user-exists" else 400
        return jsonify({"success": False, "error": messages.get(error, "修改失败")}), status

    session["user"] = new_username
    return jsonify({"success": True, "message": "用户名已更新", **_auth_payload(new_username)})


@bp.route("/api/auth/account/password", methods=["POST"])
def account_update_password():
    username = _account_username()
    if not username:
        return jsonify({"success": False, "error": "请先登录"}), 401
    if get_user_role(username) == "admin":
        return jsonify({"success": False, "error": "管理员请在后台管理账户"}), 403

    data = request.get_json(silent=True) or {}
    current_password = str(data.get("current_password") or "")
    new_password = str(data.get("new_password") or "")
    confirmation = str(data.get("new_password_confirm") or "")
    password_error = _password_error(new_password, confirmation)
    if password_error:
        return jsonify({"success": False, "error": password_error}), 400
    ok, error = update_password_with_current(username, current_password, new_password)
    if not ok:
        message = "当前密码不正确" if error == "invalid-password" else "账户不存在"
        return jsonify({"success": False, "error": message}), 400
    return jsonify({"success": True, "message": "密码已更新"})


@bp.route("/forgot-password")
def forgot_password_page():
    return render_template("auth_forgot.html")


@bp.route("/reset-password")
def reset_password_page():
    token = str(request.args.get("token") or "").strip()
    record = get_password_reset(token) if token else None
    return render_template(
        "auth_reset.html",
        reset_token=token if record else "",
        reset_username=record.get("username") if record else "",
        reset_valid=bool(record),
    )


@bp.route("/api/auth/register/start", methods=["POST"])
def register_start():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    email = normalize_email(data.get("email"))
    password = str(data.get("password") or "")
    confirmation = str(data.get("password_confirm") or "")

    if not valid_username(username):
        return jsonify({"success": False, "error": "用户名需为 3–32 位字母、数字、中文、点、横线或下划线"}), 400
    if not EMAIL_PATTERN.fullmatch(email) or len(email) > 254:
        return jsonify({"success": False, "error": "请输入有效的邮箱地址"}), 400
    password_error = _password_error(password, confirmation)
    if password_error:
        return jsonify({"success": False, "error": password_error}), 400
    if user_exists(username):
        return jsonify({"success": False, "error": "用户名已被使用"}), 409
    if email_exists(email):
        return jsonify({"success": False, "error": "该邮箱已经注册"}), 409

    flow_id, code, flow_error = create_registration(
        username,
        email,
        generate_password_hash(password),
        client_id=_client_id(),
    )
    if flow_error == "rate-limited":
        return jsonify({"success": False, "error": "发送过于频繁，请稍后再试"}), 429
    if not _send_account_email(
        email,
        "你的账户注册验证码",
        f"你好，{username}。你的注册验证码是 {code}，10 分钟内有效。",
        _registration_email(username, code),
    ):
        cancel_registration(flow_id)
        return jsonify({"success": False, "error": "验证码邮件发送失败，请稍后重试"}), 502
    return jsonify({
        "success": True,
        "flow_id": flow_id,
        "email": _mask_email(email),
        "expires_in": REGISTRATION_TTL_SECONDS,
    })


@bp.route("/api/auth/register/verify", methods=["POST"])
def register_verify():
    data = request.get_json(silent=True) or {}
    flow_id = str(data.get("flow_id") or "").strip()
    code = re.sub(r"\s+", "", str(data.get("code") or ""))
    if not re.fullmatch(r"\d{6}", code):
        return jsonify({"success": False, "error": "请输入 6 位数字验证码"}), 400

    record, verification_error = verify_registration_code(flow_id, code)
    if verification_error:
        messages = {
            "invalid-code": "验证码不正确",
            "too-many-attempts": "验证码错误次数过多，请重新注册",
            "invalid-or-expired": "验证码已失效，请重新注册",
        }
        return jsonify({"success": False, "error": messages.get(verification_error, "验证失败")}), 400

    ok, create_error = create_user_from_password_hash(
        record.get("username"),
        record.get("password_hash"),
        ROLE_GUEST,
        email=record.get("email"),
    )
    if not ok:
        consume_registration(flow_id)
        message = "用户名已被使用" if create_error == "user-exists" else "该邮箱已经注册"
        return jsonify({"success": False, "error": message}), 409

    consume_registration(flow_id)
    username = record["username"]
    session["logged_in"] = True
    session["user"] = username
    session["role"] = ROLE_GUEST
    session["login_time"] = int(time.time())
    return jsonify({"success": True, "status": "success", **_auth_payload(username)})


@bp.route("/api/auth/password/forgot", methods=["POST"])
def password_forgot():
    email = normalize_email((request.get_json(silent=True) or {}).get("email"))
    if not EMAIL_PATTERN.fullmatch(email) or len(email) > 254:
        return jsonify({"success": False, "error": "请输入有效的邮箱地址"}), 400

    username = get_username_by_email(email)
    if username:
        token, flow_error = create_password_reset(username, email, client_id=_client_id())
        if not flow_error:
            reset_url = f"{_public_base_url()}/reset-password?token={token}"
            delivered = _send_account_email(
                email,
                "找回你的账户与密码",
                f"账户名：{username}\n请在 30 分钟内打开以下链接设置新密码：\n{reset_url}",
                _reset_email(username, reset_url),
            )
            if not delivered:
                take_password_reset(token)
    return jsonify({"success": True, "message": GENERIC_RESET_MESSAGE, "expires_in": RESET_TTL_SECONDS})


@bp.route("/api/auth/password/reset", methods=["POST"])
def password_reset():
    data = request.get_json(silent=True) or {}
    token = str(data.get("token") or "").strip()
    password = str(data.get("password") or "")
    confirmation = str(data.get("password_confirm") or "")
    password_error = _password_error(password, confirmation)
    if password_error:
        return jsonify({"success": False, "error": password_error}), 400

    record = take_password_reset(token)
    if not record or not user_exists(record.get("username")):
        return jsonify({"success": False, "error": "重置链接无效或已经过期"}), 400
    ok, _ = update_user_password(record["username"], password)
    if not ok:
        return jsonify({"success": False, "error": "账户不存在"}), 404
    session.clear()
    return jsonify({"success": True, "message": "密码已更新，请重新登录"})


@bp.route("/api/auth/status")
def auth_status():
    if session.get("logged_in") and not user_exists(session.get("user")):
        session.clear()
    role = get_user_role(session.get("user")) if session.get("logged_in") else None
    if session.get("logged_in"):
        if not role:
            session.clear()
            role = None
        elif session.get("role") != role:
            session["role"] = role
    username = session.get("user") if session.get("logged_in") else None
    return jsonify(_auth_payload(username))


@bp.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    username = data.get("username")
    password = data.get("password")

    if verify_user_password(username, password):
        session["logged_in"] = True
        session["user"] = username
        session["role"] = get_user_role(username)
        session["login_time"] = int(time.time())
        return jsonify({"success": True, "status": "success", **_auth_payload(username)})

    return jsonify({"success": False, "status": "error", "message": "用户名或密码错误"}), 401


@bp.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True, "status": "success"})
