"""Authenticated HTTP API for reusable RTC sessions."""

from urllib.parse import urlsplit

from flask import Blueprint, jsonify, render_template, request, session

from modules.auth.user_store import get_user_id, user_exists
from modules.realtime import socketio
from modules.rtc.ice import ice_config
from modules.rtc.registry import RegistryError, registry


bp = Blueprint("rtc", __name__)


@bp.after_request
def rtc_security_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.path == "/rtc":
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; base-uri 'self'"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(self), display-capture=(self)"
    return response


def _account():
    username = str(session.get("user") or "") if session.get("logged_in") else ""
    if not username or not user_exists(username):
        session.clear()
        return None
    return {
        "username": username,
        "principal": f"user:{get_user_id(username)}",
    }


def _json_request_allowed():
    if not request.is_json:
        return False
    origin = request.headers.get("Origin")
    if not origin:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == request.host.lower()


def _error(error, status=None):
    code = str(error)
    if status is None:
        status = {
            "forbidden": 403,
            "session-not-found": 404,
            "invalid-invite": 404,
            "already-member": 409,
            "session-full": 409,
        }.get(code, 400)
    return jsonify({"ok": False, "error": code}), status


def _require_api_account():
    account = _account()
    if not account:
        return None, _error("login-required", 401)
    return account, None


def _require_json_account():
    account, error = _require_api_account()
    if error:
        return None, error
    if not _json_request_allowed():
        return None, _error("invalid-request-origin", 403)
    return account, None


@bp.route("/rtc")
def page():
    return render_template("rtc.html")


@bp.route("/api/rtc/sessions", methods=["POST"])
def create_session():
    account, error = _require_json_account()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    try:
        created, join_token = registry.create_session(
            account["principal"],
            account["username"],
            kind=data.get("kind", "call"),
            role=data.get("role", "duplex"),
            max_participants=data.get("max_participants", 2),
            ttl=data.get("ttl", 3600),
        )
    except (RegistryError, TypeError, ValueError) as exc:
        return _error(exc)
    return jsonify({"ok": True, "session": created, "join_token": join_token}), 201


@bp.route("/api/rtc/sessions/<session_id>")
def session_status(session_id):
    account, error = _require_api_account()
    if error:
        return error
    try:
        rtc_session = registry.session_for_principal(session_id, account["principal"])
    except RegistryError as exc:
        return _error(exc)
    return jsonify({"ok": True, "session": rtc_session})


@bp.route("/api/rtc/sessions/<session_id>/invites", methods=["POST"])
def create_invite(session_id):
    account, error = _require_json_account()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    try:
        token, expires_at = registry.create_invite(
            session_id,
            account["principal"],
            role=data.get("role", "duplex"),
            ttl=data.get("ttl", 600),
        )
    except (RegistryError, TypeError, ValueError) as exc:
        return _error(exc)
    # Fragment credentials are not sent in HTTP requests or normal access logs.
    return jsonify({
        "ok": True,
        "invite_token": token,
        "invite_url": f"/rtc#invite={token}",
        "expires_at": expires_at,
    }), 201


@bp.route("/api/rtc/invites/redeem", methods=["POST"])
def redeem_invite():
    account, error = _require_json_account()
    if error:
        return error
    token = str((request.get_json(silent=True) or {}).get("invite_token") or "")
    try:
        rtc_session, join_token = registry.redeem_invite(token, account["principal"], account["username"])
    except RegistryError as exc:
        return _error(exc)
    return jsonify({"ok": True, "session": rtc_session, "join_token": join_token})


@bp.route("/api/rtc/sessions/<session_id>/join-token", methods=["POST"])
def issue_join_token(session_id):
    account, error = _require_json_account()
    if error:
        return error
    try:
        token = registry.issue_join_token(session_id, account["principal"])
    except RegistryError as exc:
        return _error(exc)
    return jsonify({"ok": True, "join_token": token, "expires_in": 60})


@bp.route("/api/rtc/sessions/<session_id>/ice-config")
def get_ice_config(session_id):
    account, error = _require_api_account()
    if error:
        return error
    try:
        registry.session_for_principal(session_id, account["principal"])
    except RegistryError as exc:
        return _error(exc)
    response = jsonify({"ok": True, **ice_config(account["principal"].split(":", 1)[-1])})
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/api/rtc/sessions/<session_id>", methods=["DELETE"])
def end_session(session_id):
    account, error = _require_json_account()
    if error:
        return error
    try:
        target_sids = registry.end_session(session_id, account["principal"])
    except RegistryError as exc:
        return _error(exc)
    for sid in target_sids:
        socketio.emit("rtc_session_ended", {"session_id": session_id}, to=sid, namespace="/rtc")
    return jsonify({"ok": True})
