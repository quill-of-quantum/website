from functools import wraps
import shutil
import subprocess
import time
from datetime import datetime, timedelta

import requests
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from modules.auth.user_store import is_admin_user, user_exists
from modules.weather.db import activate_period, create_period, update_period_location
from modules.weather.store import get_config, latest_run, set_homepage_visible, start_analysis


bp = Blueprint("weather", __name__)


def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if session.get("logged_in") and not user_exists(session.get("user")):
            session.clear()
        if not session.get("logged_in") or not is_admin_user(session.get("user")):
            if request.path.startswith("/1/api"):
                return jsonify({"ok": False, "require_login": True}), 403
            return redirect(url_for("index"))
        return function(*args, **kwargs)
    return wrapper


def _service_state():
    systemctl = shutil.which("systemctl", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if not systemctl:
        return {"active": False, "status": "unknown"}
    try:
        result = subprocess.run(
            [systemctl, "show", "weather.service", "-p", "LoadState", "-p", "ActiveState", "-p", "SubState", "-p", "UnitFileState"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        active = values.get("ActiveState") in ("active", "activating")
        return {
            "active": active,
            "status": "运行中" if active else "空闲（按需启动）",
            "loaded": values.get("LoadState", "unknown"),
        }
    except (OSError, subprocess.SubprocessError):
        return {"active": False, "status": "unknown"}


@bp.get("/1/weather")
@login_required
def page():
    return render_template("weather.html")


@bp.get("/1/api/weather")
@login_required
def settings():
    return jsonify({"ok": True, "config": get_config(), "run": latest_run(), "service": _service_state()})


@bp.post("/1/api/weather/settings")
@login_required
def update_settings():
    payload = request.get_json(silent=True) or {}
    if "homepage_visible" not in payload:
        return jsonify({"ok": False, "error": "缺少主页显示设置"}), 400
    set_homepage_visible(bool(payload["homepage_visible"]))
    return jsonify({"ok": True, "config": get_config()})


@bp.post("/1/api/weather/periods")
@login_required
def add_period():
    payload = request.get_json(silent=True) or {}
    current = get_config()["active_period"]
    try:
        period_id = create_period(
            payload.get("name"), payload.get("starts_at"),
            location_name=current["location_name"], latitude=current["latitude"],
            longitude=current["longitude"], timezone=current["timezone"],
        )
    except (ValueError, TypeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "period_id": period_id, "config": get_config()})


@bp.post("/1/api/weather/periods/<int:period_id>/location")
@login_required
def change_period_location(period_id):
    payload = request.get_json(silent=True) or {}
    periods = get_config()["periods"]
    current = next((item for item in periods if item["id"] == period_id), None)
    if not current:
        return jsonify({"ok": False, "error": "指定周期不存在"}), 404
    try:
        changed = update_period_location(
            period_id, current["location_name"], payload.get("latitude"),
            payload.get("longitude"), "auto",
        )
    except (ValueError, TypeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    message = "周期地点已保存；旧地点的天气缓存已清除" if changed else "周期地点未变化，现有天气缓存已保留"
    return jsonify({"ok": True, "config": get_config(), "message": message})


@bp.post("/1/api/weather/periods/<int:period_id>/activate")
@login_required
def select_period(period_id):
    try:
        activate_period(period_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    return jsonify({"ok": True, "config": get_config()})


@bp.post("/1/api/weather/run")
@login_required
def run_analysis():
    if latest_run().get("status") == "running":
        return jsonify({"ok": False, "error": "已有分析正在运行"}), 409
    try:
        start_analysis("manual")
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    return jsonify({"ok": True, "message": "天气与用量分析已立即启动"})


def _probe(name, url):
    started = time.monotonic()
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        payload = response.json()
        valid = bool(payload.get("daily") or payload.get("hourly"))
        return {"name": name, "ok": valid, "status_code": response.status_code, "duration_ms": round((time.monotonic() - started) * 1000)}
    except (requests.RequestException, ValueError) as exc:
        return {"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}", "duration_ms": round((time.monotonic() - started) * 1000)}


@bp.post("/1/api/weather/check")
@login_required
def check_apis():
    period = get_config()["active_period"]
    coordinates = f"latitude={period['latitude']}&longitude={period['longitude']}&timezone={period['timezone']}"
    base = "https://api.open-meteo.com/v1/forecast?" + coordinates
    archive_day = (datetime.now().astimezone().date() - timedelta(days=7)).isoformat()
    checks = [
        _probe("Open-Meteo 小时气温", base + "&hourly=temperature_2m&forecast_days=1"),
        _probe("Open-Meteo 每日预报", base + "&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean&forecast_days=1"),
        _probe("Open-Meteo 历史天气", "https://archive-api.open-meteo.com/v1/archive?" + coordinates + f"&start_date={archive_day}&end_date={archive_day}&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean"),
    ]
    return jsonify({"ok": all(item["ok"] for item in checks), "checks": checks})


@bp.get("/api/weather/display")
def display_settings():
    config = get_config()
    return jsonify({"homepage_visible": config["homepage_visible"]})
