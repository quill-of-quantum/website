import os

import requests
from flask import Blueprint, jsonify, render_template, request


bp = Blueprint("email", __name__)

EMAIL_SERVICE_URL = os.getenv("EMAIL_SERVICE_URL", "http://127.0.0.1:8081")


@bp.route("/mail", methods=["GET"])
def mail_page():
    return render_template("mail.html")


@bp.route("/api/mail/send", methods=["POST"])
def send_mail():
    payload = request.get_json(silent=True) or {}
    to = (payload.get("to") or "").strip()
    subject = (payload.get("subject") or "").strip()
    text = (payload.get("text") or "").strip()
    html = (payload.get("html") or "").strip()

    if not to or not subject or not text:
        return jsonify({"error": "to, subject and text are required"}), 400

    try:
        response = requests.post(
            f"{EMAIL_SERVICE_URL}/api/mail/send",
            json={"to": to, "subject": subject, "text": text, "html": html},
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
