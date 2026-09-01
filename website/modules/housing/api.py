from functools import wraps
import os
import shutil
import subprocess

import requests
from flask import Blueprint, jsonify, redirect, render_template, request, send_file, session, url_for

from modules.auth.user_store import is_admin_user, user_exists
from modules.housing.store import load_config, load_state, request_run, save_config, service_pid_alive
from modules.housing.result import RESULT_PATH, generate_result_html
from modules.housing.db import (
    latest_matching_change_batch, notification_rooms, recent_display_changes,
)
from modules.housing.notifications import build_notification, notification_title


bp = Blueprint("housing", __name__)
EMAIL_SERVICE_URL = os.getenv("EMAIL_SERVICE_URL", "http://127.0.0.1:8081")


def _service_state():
    pid_active, pid = service_pid_alive()
    systemctl = shutil.which(
        "systemctl",
        path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    )
    if not systemctl:
        return {
            "status": (f"active (pid {pid})" if pid_active else "no-systemctl"),
            "active": pid_active, "enabled": "unknown", "loaded": "unknown",
        }
    result = subprocess.run(
        [systemctl, "show", "housing_tracker.service", "-p", "LoadState", "-p", "ActiveState", "-p", "SubState", "-p", "UnitFileState"],
        capture_output=True, text=True, check=False, timeout=5,
    )
    values = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    active = values.get("ActiveState", "unknown")
    sub = values.get("SubState", "unknown")
    if active == "unknown" and pid_active:
        return {
            "status": f"active (pid {pid})", "active": True,
            "enabled": values.get("UnitFileState", "unknown"),
            "loaded": values.get("LoadState", "unknown"),
        }
    return {
        "status": f"{active} ({sub})", "active": active == "active",
        "enabled": values.get("UnitFileState", "unknown"),
        "loaded": values.get("LoadState", "unknown"),
    }


def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if session.get("logged_in") and not user_exists(session.get("user")):
            session.clear()
        if not session.get("logged_in") or not is_admin_user(session.get("user")):
            if request.path.startswith("/1/api"):
                return jsonify({"require_login": True}), 403
            return redirect(url_for("index"))
        return function(*args, **kwargs)
    return wrapper


@bp.get("/1/housing")
@login_required
def page():
    return render_template("housing.html")


@bp.get("/1/api/housing")
@login_required
def get_settings():
    return jsonify({
        "ok": True, "config": load_config(), "state": load_state(),
        "service": _service_state(), "recent_changes": recent_display_changes(),
    })


@bp.post("/1/api/housing")
@login_required
def update_settings():
    try:
        config = save_config(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "config": config})


@bp.post("/1/api/housing/run")
@login_required
def run_now():
    service = _service_state()
    if not service.get("active"):
        return jsonify({
            "ok": False,
            "error": "租房追踪进程尚未运行，请先回到管理面板的“进程开关”启用它",
            "service": service,
        }), 409
    state = load_state()
    if state.get("status") == "running":
        return jsonify({"ok": False, "error": "当前已有一次检查正在运行"}), 409
    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode") or "incremental")
    if mode not in ("full", "incremental"):
        return jsonify({"ok": False, "error": "搜索模式无效"}), 400
    request_run(mode)
    label = "全量" if mode == "full" else "增量"
    return jsonify({"ok": True, "message": f"已提交一次{label}搜索，进度将在下方实时更新"})


@bp.post("/1/api/housing/initialize")
@login_required
def initialize_database():
    service = _service_state()
    if not service.get("active"):
        return jsonify({"ok": False, "error": "租房追踪进程尚未运行，无法执行初始化"}), 409
    if load_state().get("status") == "running":
        return jsonify({"ok": False, "error": "当前已有一次检查正在运行，请完成后再初始化"}), 409
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return jsonify({"ok": False, "error": "初始化需要明确确认"}), 400
    request_run("initialize")
    return jsonify({
        "ok": True,
        "message": "已提交初始化：将清理房源历史、保留地理编码缓存，并建立全量未变化基准",
    })


@bp.post("/1/api/housing/resend-notification")
@login_required
def resend_notification():
    config = load_config()
    recipients = config.get("notification_emails") or []
    if not recipients:
        return jsonify({"ok": False, "error": "请先配置并保存至少一个通知收件地址"}), 400
    # 与邮件是否曾发送成功无关，只查找最近一批符合当前通知设置的实际房源变化。
    selected = latest_matching_change_batch(
        config.get("notify_added_types", []), config.get("notify_delisted_types", []),
    )
    if not selected:
        return jsonify({"ok": False, "error": "数据库中还没有符合通知设置的历史房源变化"}), 404

    rooms = notification_rooms([item["id"] for item in selected])
    text, html, _ = build_notification(selected, rooms)
    failures = []
    for recipient in recipients:
        try:
            response = requests.post(
                f"{EMAIL_SERVICE_URL}/api/mail/send/default",
                json={
                    "to": [recipient],
                    "cc": [],
                    "bcc": [],
                    "subject": f"{notification_title(selected)}（重发 · {len(selected)} 条）",
                    "text": text,
                    "html": html,
                },
                timeout=20,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            failures.append(f"{recipient}: {exc}")
    if failures:
        return jsonify({"ok": False, "error": "部分邮件发送失败：" + "；".join(failures)}), 502
    return jsonify({
        "ok": True,
        "message": f"已将最近一次变化邮件（{len(selected)} 条）发送到 {len(recipients)} 个配置邮箱",
    })


@bp.get("/1/housing/result")
@login_required
def result_page():
    # 样例文件只作为视觉参考；实际页面每次从 SQLite 重新生成。
    path = generate_result_html()
    return send_file(path, mimetype="text/html", conditional=False)
