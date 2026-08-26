from collections import deque
from datetime import datetime, timedelta
import os
import shutil
import subprocess
import threading
import time

import psutil
from flask import Response, jsonify, render_template, send_from_directory


WEATHER_DATA_DIR = "/home/bbdwz/projects/website/data/weather"
WEATHER_OUTPUT_DIR = "/home/bbdwz/projects/website/data/weather"

SYSINFO_CACHE = {
    "timestamps": deque(maxlen=30),
    "cpu": deque(maxlen=30),
    "memory": deque(maxlen=30),
    "temperature": deque(maxlen=30),
    "latest": None,
}
SYSINFO_LOCK = threading.Lock()
SYSINFO_COLLECTOR_STARTED = False


def index():
    """主页"""
    from modules.weather.store import get_config
    return render_template("index.html", weather_homepage_visible=get_config()["homepage_visible"])


def cloud():
    """云盘页"""
    return render_template("cloud.html")


def tracker_ui():
    """追踪页"""
    return render_template("tracker.html")


def vision_ui():
    """智能视觉检测页"""
    return render_template("tool_1.html")


def viewer():
    """3D 模型调试页面"""
    return render_template("viewer.html")


def map_ui():
    """路线规划页"""
    return render_template("map.html")

def aggregate_map_search_ui():
    """地图多关键词聚合搜索页。"""
    return render_template("map_aggregate_search.html")


def route_creator_ui():
    """路线创作页"""
    return render_template("route_creator.html")


def _read_system_info():
    data = {}

    data["cpu_percent"] = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    data["memory_percent"] = mem.percent
    data["memory_used"] = round(mem.used / (1024 ** 2), 1)
    data["memory_total"] = round(mem.total / (1024 ** 2), 1)

    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            data["temperature"] = round(int(f.read()) / 1000.0, 1)
    except FileNotFoundError:
        data["temperature"] = None

    iwconfig_path = shutil.which("iwconfig")
    if iwconfig_path:
        try:
            iw_output = subprocess.check_output([iwconfig_path], text=True, stderr=subprocess.DEVNULL, timeout=1)
            line = [l for l in iw_output.split("\n") if "Signal level" in l]
            data["wifi_signal"] = line[0].split("Signal level=")[-1].split()[0] if line else None
        except Exception:
            data["wifi_signal"] = None
    else:
        data["wifi_signal"] = None

    uptime_seconds = time.time() - psutil.boot_time()
    data["uptime"] = str(timedelta(seconds=int(uptime_seconds)))
    return data


def collect_system_info():
    """后台线程：定期采集系统信息"""
    while True:
        try:
            data = _read_system_info()

            timestamp = datetime.now().strftime("%H:%M:%S")
            with SYSINFO_LOCK:
                SYSINFO_CACHE["latest"] = data
                SYSINFO_CACHE["timestamps"].append(timestamp)
                SYSINFO_CACHE["cpu"].append(data["cpu_percent"])
                SYSINFO_CACHE["memory"].append(data["memory_percent"])
                SYSINFO_CACHE["temperature"].append(data["temperature"])

            time.sleep(3)
        except Exception as e:
            print(f"采集系统信息出错: {e}")
            time.sleep(3)


def start_system_info_collector():
    global SYSINFO_COLLECTOR_STARTED
    if SYSINFO_COLLECTOR_STARTED:
        return
    SYSINFO_COLLECTOR_STARTED = True
    threading.Thread(target=collect_system_info, daemon=True).start()


def system_info():
    """系统状态 API - 返回当前值和历史数据"""
    with SYSINFO_LOCK:
        latest = dict(SYSINFO_CACHE["latest"] or {})
        history = {
            "timestamps": list(SYSINFO_CACHE["timestamps"]),
            "cpu": list(SYSINFO_CACHE["cpu"]),
            "memory": list(SYSINFO_CACHE["memory"]),
            "temperature": list(SYSINFO_CACHE["temperature"]),
        }
    data = latest or _read_system_info()
    data["history"] = history
    return jsonify(data)


def weather_chart_file(filename):
    return _send_weather_file(filename)


def serve_weather_file(filename):
    return _send_weather_file(filename)


def _weather_base_dir(filename):
    if filename.endswith(".svg"):
        return WEATHER_OUTPUT_DIR
    return WEATHER_DATA_DIR


def _send_weather_file(filename):
    allowed = {
        "usage_cumulative.svg", "usage_forecast.svg", "usage_hourly.svg",
        "usage_daily.svg", "usage_pattern.svg", "usage_pattern_polar.svg",
        "usage_heatmap.svg", "forecast_usage.csv", "daily_usage.csv",
        "hourly_usage.csv",
    }
    if filename not in allowed:
        return "File not found", 404
    base_dir = _weather_base_dir(filename)
    full_path = os.path.join(base_dir, filename)
    if not os.path.exists(full_path):
        return "File not found", 404
    if filename.endswith(".svg"):
        with open(full_path, "r", encoding="utf-8") as f:
            return Response(f.read(), mimetype="image/svg+xml")
    return send_from_directory(base_dir, filename)


def register_routes(app):
    app.add_url_rule("/", "index", index)
    app.add_url_rule("/cloud", "cloud", cloud)
    app.add_url_rule("/tracker", "tracker_ui", tracker_ui)
    app.add_url_rule("/vision", "vision_ui", vision_ui)
    app.add_url_rule("/viewer", "viewer", viewer)
    app.add_url_rule("/map", "map_ui", map_ui)
    app.add_url_rule("/map/aggregate-search", "aggregate_map_search_ui", aggregate_map_search_ui)
    app.add_url_rule("/route_creator", "route_creator_ui", route_creator_ui)
    app.add_url_rule("/api/system", "system_info", system_info)
    app.add_url_rule("/weather_chart/<path:filename>", "weather_chart_file", weather_chart_file)
    app.add_url_rule("/weather/<path:filename>", "serve_weather_file", serve_weather_file)
