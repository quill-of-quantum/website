import time

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash


bp = Blueprint("auth", __name__)

USER_DB = {
    "admin": generate_password_hash("bbdwz"),
}


@bp.route("/api/auth/status")
def auth_status():
    return jsonify({
        "logged_in": session.get("logged_in", False),
        "user": session.get("user") if session.get("logged_in") else None,
    })


@bp.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    username = data.get("username")
    password = data.get("password")

    user_hash = USER_DB.get(username)
    if user_hash and check_password_hash(user_hash, password):
        session["logged_in"] = True
        session["user"] = username
        session["login_time"] = int(time.time())
        return jsonify({"success": True, "status": "success", "user": username})

    return jsonify({"success": False, "status": "error", "message": "用户名或密码错误"}), 401


@bp.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True, "status": "success"})
