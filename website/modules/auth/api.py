import time

from flask import Blueprint, jsonify, request, session

from modules.auth.user_store import get_user_role, user_exists, verify_user_password


bp = Blueprint("auth", __name__)


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
    return jsonify({
        "logged_in": session.get("logged_in", False),
        "user": session.get("user") if session.get("logged_in") else None,
        "role": role,
        "is_admin": role == "admin" if session.get("logged_in") else False,
    })


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
        return jsonify({"success": True, "status": "success", "user": username, "role": session["role"]})

    return jsonify({"success": False, "status": "error", "message": "用户名或密码错误"}), 401


@bp.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True, "status": "success"})
