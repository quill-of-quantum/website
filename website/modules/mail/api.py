import os
from functools import wraps

import requests
from flask import Blueprint, jsonify, redirect, render_template, request, session

from modules.auth.user_store import is_admin_user, user_exists


bp = Blueprint("email", __name__)
EMAIL_SERVICE_URL = os.getenv("EMAIL_SERVICE_URL", "http://127.0.0.1:8081").rstrip("/")


def _require_login(admin=False):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = session.get("user")
            valid = session.get("logged_in") and user_exists(user)
            if not valid or (admin and not is_admin_user(user)):
                if request.path.startswith("/api/") or request.path.startswith("/1/api/"):
                    return jsonify({"success": False, "error": "login-required"}), 403
                return redirect("/")
            return view(*args, **kwargs)
        return wrapper
    return decorator


def _service_request(method, path, *, json=None, timeout=20):
    try:
        response = requests.request(method, f"{EMAIL_SERVICE_URL}{path}", json=json, timeout=timeout)
    except requests.RequestException:
        return None, (jsonify({"success": False, "error": "email-service-unavailable"}), 502)
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {"error": "invalid-email-service-response"}
    if response.status_code >= 400:
        return None, (jsonify({"success": False, "error": payload.get("error", "email-service-error")}), response.status_code)
    return payload, None


def _addresses(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").replace(";", ",").split(",") if item.strip()]


def _mask_email(value):
    local, separator, domain = str(value or "").partition("@")
    if not separator:
        return ""
    visible = local[:2]
    return f"{visible}***@{domain}"


def _send_payload(source, include_account=False):
    payload = {
        "to": _addresses(source.get("to")),
        "cc": _addresses(source.get("cc")),
        "bcc": _addresses(source.get("bcc")),
        "subject": str(source.get("subject") or "").strip(),
        "text": str(source.get("text") or "").strip(),
        "html": str(source.get("html") or "").strip(),
    }
    if include_account:
        payload["accountId"] = str(source.get("accountId") or "").strip()
    return payload


@bp.route("/mail")
def mail_page():
    return render_template("mail.html")


@bp.route("/1/mail")
@_require_login(admin=True)
def admin_mail_page():
    return render_template("mail_admin.html")


@bp.route("/api/mail/accounts")
def mail_accounts():
    payload, error = _service_request("GET", "/api/mail/accounts")
    if error:
        return error
    if not (session.get("logged_in") and is_admin_user(session.get("user"))):
        default_id = payload.get("defaultAccountId")
        default = (payload.get("accounts") or {}).get(default_id)
        if default:
            default = {
                "id": default.get("id"),
                "displayName": default.get("displayName"),
                "address": _mask_email(default.get("address")),
                "enabled": default.get("enabled"),
                "configured": default.get("configured"),
            }
        payload["accounts"] = {default_id: default} if default_id and default else {}
        payload.pop("forwardingRules", None)
        payload.pop("forwardingExecutions", None)
    return jsonify({"success": True, **payload})


@bp.route("/api/mail/send", methods=["POST"])
def send_mail():
    source = request.get_json(silent=True) or {}
    payload = _send_payload(source)
    if not any((payload["to"], payload["cc"], payload["bcc"])):
        return jsonify({"success": False, "error": "recipient-required"}), 400
    if not payload["subject"] or not payload["text"]:
        return jsonify({"success": False, "error": "subject-and-text-required"}), 400
    result, error = _service_request("POST", "/api/mail/send/default", json=payload, timeout=30)
    return error or jsonify({"success": True, **result})


@bp.route("/1/api/mail/send", methods=["POST"])
@_require_login(admin=True)
def admin_send_mail():
    payload = _send_payload(request.get_json(silent=True) or {}, include_account=True)
    if not payload["accountId"] or not any((payload["to"], payload["cc"], payload["bcc"])):
        return jsonify({"success": False, "error": "account-and-recipient-required"}), 400
    if not payload["subject"] or not payload["text"]:
        return jsonify({"success": False, "error": "subject-and-text-required"}), 400
    result, error = _service_request("POST", "/api/mail/send", json=payload, timeout=30)
    return error or jsonify({"success": True, **result})


@bp.route("/1/api/mail/default-account", methods=["PUT"])
@_require_login(admin=True)
def admin_default_account():
    account_id = str((request.get_json(silent=True) or {}).get("accountId") or "").strip()
    result, error = _service_request("PUT", "/api/mail/default-account", json={"accountId": account_id})
    return error or jsonify({"success": True, **result})


@bp.route("/1/api/mail/accounts/<account_id>/messages")
@_require_login(admin=True)
def admin_mail_messages(account_id):
    limit = max(1, min(request.args.get("limit", 20, type=int), 100))
    payload, error = _service_request("GET", f"/api/mail/accounts/{account_id}/messages?limit={limit}", timeout=30)
    return error or jsonify({"success": True, **payload})


@bp.route("/1/api/mail/accounts/<account_id>/messages/<int:uid>")
@_require_login(admin=True)
def admin_mail_message(account_id, uid):
    payload, error = _service_request("GET", f"/api/mail/accounts/{account_id}/messages/{uid}", timeout=30)
    return error or jsonify({"success": True, **payload})


@bp.route("/1/api/mail/accounts/<account_id>/test", methods=["POST"])
@_require_login(admin=True)
def admin_mail_test(account_id):
    payload, error = _service_request("POST", f"/api/mail/accounts/{account_id}/test", timeout=35)
    return error or jsonify({"success": True, **payload})


@bp.route("/1/api/mail/forwarding", methods=["POST"])
@_require_login(admin=True)
def admin_create_forwarding():
    source = request.get_json(silent=True) or {}
    payload = {
        "enabled": bool(source.get("enabled")),
        "sourceAccountId": str(source.get("sourceAccountId") or "").strip(),
        "sendAccountId": str(source.get("sendAccountId") or "").strip(),
        "recipients": _addresses(source.get("recipients")),
    }
    result, error = _service_request("POST", "/api/mail/forwarding", json=payload)
    return error or jsonify({"success": True, **result})


@bp.route("/1/api/mail/forwarding/<rule_id>", methods=["PUT", "DELETE"])
@_require_login(admin=True)
def admin_forwarding(rule_id):
    if request.method == "DELETE":
        result, error = _service_request("DELETE", f"/api/mail/forwarding/{rule_id}")
        return error or jsonify({"success": True, **result})
    source = request.get_json(silent=True) or {}
    payload = {
        "enabled": bool(source.get("enabled")),
        "sourceAccountId": str(source.get("sourceAccountId") or "").strip(),
        "sendAccountId": str(source.get("sendAccountId") or "").strip(),
        "recipients": _addresses(source.get("recipients")),
    }
    result, error = _service_request("PUT", f"/api/mail/forwarding/{rule_id}", json=payload)
    return error or jsonify({"success": True, **result})
