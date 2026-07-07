import os

import requests
from flask import Blueprint, jsonify, request


bp = Blueprint("email", __name__, url_prefix="/api/mail")

EMAIL_SERVICE_URL = os.getenv("EMAIL_SERVICE_URL", "http://127.0.0.1:8081")


@bp.route("/send", methods=["POST"])
def send_mail():
    payload = request.get_json(silent=True) or {}
    to = (payload.get("to") or "").strip()
    subject = (payload.get("subject") or "").strip()
    text = (payload.get("text") or "").strip()

    if not to or not subject or not text:
        return jsonify({"error": "to, subject and text are required"}), 400

    try:
        response = requests.post(
            f"{EMAIL_SERVICE_URL}/api/mail/send",
            json={"to": to, "subject": subject, "text": text},
            timeout=20,
        )
    except requests.RequestException as exc:
        return jsonify({"error": "email service unavailable", "detail": str(exc)}), 502

    if response.status_code >= 400:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        return jsonify({"error": "email service rejected request", "detail": detail}), response.status_code

    return jsonify(response.json() if response.content else {"status": "sent"})
