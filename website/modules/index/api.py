from collections import deque
from datetime import datetime, timedelta
import os
import subprocess
import time
import xml.etree.ElementTree as ET

import psutil
import requests
from flask import Response, jsonify, render_template, send_from_directory


WEATHER_DATA_DIR = "/home/bbdwz/projects/website/data/weather"
WEATHER_OUTPUT_DIR = "/home/bbdwz/projects/website/data/weather"
EXCHANGE_RATE_CACHE_TTL = 600
EXCHANGE_RATE_CACHE = {
    "ts": 0,
    "data": None,
}

SYSINFO_CACHE = {
    "timestamps": deque(maxlen=30),
    "cpu": deque(maxlen=30),
    "memory": deque(maxlen=30),
    "temperature": deque(maxlen=30),
}


def index():
    """主页"""
    return render_template("index.html")


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


def route_creator_ui():
    """路线创作页"""
    return render_template("route_creator.html")


def exchange_rate_chart():
    """
    Get latest USD->CNY and EUR->CNY using open.er-api.com,
    plus recent ECB history for chart background.
    """
    now_ts = time.time()
    if EXCHANGE_RATE_CACHE["data"] and (now_ts - EXCHANGE_RATE_CACHE["ts"] < EXCHANGE_RATE_CACHE_TTL):
        return jsonify(EXCHANGE_RATE_CACHE["data"])

    latest_usd = latest_eur = None
    latest_date = None
    try:
        r = requests.get("https://open.er-api.com/v6/latest/CNY", timeout=5)
        data = r.json()
        if data.get("result") == "success":
            latest_date = data.get("time_last_update_utc", "").split(" ")[0]
            latest_eur = 1 / data["rates"]["EUR"]
            latest_usd = 1 / data["rates"]["USD"]
    except Exception:
        pass

    hist_url = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
    try:
        r = requests.get(hist_url, timeout=5)
        tree = ET.fromstring(r.content)
    except Exception:
        if EXCHANGE_RATE_CACHE["data"]:
            cached = dict(EXCHANGE_RATE_CACHE["data"])
            cached["stale"] = True
            cached["cached_at"] = datetime.utcnow().isoformat() + "Z"
            return jsonify(cached)
        return jsonify({"error": "cannot load ECB data"})

    ns = {"def": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
    data_points = []
    for cube in tree.findall(".//def:Cube/def:Cube", ns):
        date_str = cube.get("time")
        rates = {c.get("currency"): float(c.get("rate")) for c in cube.findall("def:Cube", ns)}
        if "CNY" in rates and "USD" in rates:
            eur_to_cny = rates["CNY"]
            usd_to_cny = eur_to_cny / rates["USD"]
            data_points.append((date_str, eur_to_cny, usd_to_cny))

    data_points.sort()
    dates = [datetime.strptime(d[0], "%Y-%m-%d") for d in data_points]
    eur_cny = [d[1] for d in data_points]
    usd_cny = [d[2] for d in data_points]
    if not dates:
        return jsonify({"error": "no ECB data"})

    def build_stats(values, date_list):
        min_idx, min_val = min(enumerate(values), key=lambda x: x[1])
        max_idx, max_val = max(enumerate(values), key=lambda x: x[1])
        pct_change = ((max_val - min_val) / min_val * 100) if min_val else 0
        return {
            "min": round(min_val, 4),
            "min_date": date_list[min_idx].strftime("%Y-%m-%d"),
            "min_idx": min_idx,
            "max": round(max_val, 4),
            "max_date": date_list[max_idx].strftime("%Y-%m-%d"),
            "max_idx": max_idx,
            "pct_change": round(pct_change, 2),
        }

    payload = {
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
        "eur_cny": eur_cny,
        "usd_cny": usd_cny,
        "stats": {
            "eur": build_stats(eur_cny, dates),
            "usd": build_stats(usd_cny, dates),
        },
        "latest_date": latest_date,
        "latest_eur": latest_eur,
        "latest_usd": latest_usd,
    }
    EXCHANGE_RATE_CACHE["ts"] = now_ts
    EXCHANGE_RATE_CACHE["data"] = payload
    return jsonify(payload)


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

    try:
        iw_output = subprocess.check_output("iwconfig", shell=True, text=True)
        line = [l for l in iw_output.split("\n") if "Signal level" in l]
        data["wifi_signal"] = line[0].split("Signal level=")[-1].split()[0] if line else None
    except Exception:
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
            SYSINFO_CACHE["timestamps"].append(timestamp)
            SYSINFO_CACHE["cpu"].append(data["cpu_percent"])
            SYSINFO_CACHE["memory"].append(data["memory_percent"])
            SYSINFO_CACHE["temperature"].append(data["temperature"])

            time.sleep(3)
        except Exception as e:
            print(f"采集系统信息出错: {e}")
            time.sleep(3)


def system_info():
    """系统状态 API - 返回当前值和历史数据"""
    data = _read_system_info()
    data["history"] = {
        "timestamps": list(SYSINFO_CACHE["timestamps"]),
        "cpu": list(SYSINFO_CACHE["cpu"]),
        "memory": list(SYSINFO_CACHE["memory"]),
        "temperature": list(SYSINFO_CACHE["temperature"]),
    }
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
    app.add_url_rule("/route_creator", "route_creator_ui", route_creator_ui)
    app.add_url_rule("/api/exchange_rate", "exchange_rate_chart", exchange_rate_chart)
    app.add_url_rule("/api/system", "system_info", system_info)
    app.add_url_rule("/weather_chart/<path:filename>", "weather_chart_file", weather_chart_file)
    app.add_url_rule("/weather/<path:filename>", "serve_weather_file", serve_weather_file)
