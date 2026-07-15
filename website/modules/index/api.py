from collections import deque
from datetime import datetime, timedelta
import json
import os
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

import psutil
import requests
from flask import Response, jsonify, render_template, send_from_directory


WEATHER_DATA_DIR = "/home/bbdwz/projects/website/data/weather"
WEATHER_OUTPUT_DIR = "/home/bbdwz/projects/website/data/weather"
INDEX_DATA_DIR = "/home/bbdwz/projects/website/data/index"
EXCHANGE_RATE_CACHE_PATH = os.path.join(INDEX_DATA_DIR, "exchange_rate.json")
EXCHANGE_RATE_REFRESH_INTERVAL = 30 * 60
EXCHANGE_RATE_CACHE = {
    "ts": 0,
    "data": None,
}
EXCHANGE_RATE_LOCK = threading.Lock()
EXCHANGE_RATE_REFRESH_STARTED = False

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


def _fetch_exchange_rate_payload():
    latest_usd = latest_eur = None
    latest_date = None

    try:
        r = requests.get("https://open.er-api.com/v6/latest/CNY", timeout=8)
        data = r.json()
        if data.get("result") == "success":
            latest_date = data.get("time_last_update_utc", "").split(" ")[0]
            latest_eur = 1 / data["rates"]["EUR"]
            latest_usd = 1 / data["rates"]["USD"]
    except Exception as e:
        print(f"汇率最新值获取失败: {e}")

    hist_url = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
    r = requests.get(hist_url, timeout=10)
    r.raise_for_status()
    tree = ET.fromstring(r.content)

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
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    return payload


def _write_exchange_rate_cache(payload):
    os.makedirs(INDEX_DATA_DIR, exist_ok=True)
    tmp_path = EXCHANGE_RATE_CACHE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp_path, EXCHANGE_RATE_CACHE_PATH)


def _read_exchange_rate_cache_file():
    if not os.path.exists(EXCHANGE_RATE_CACHE_PATH):
        return None
    try:
        with open(EXCHANGE_RATE_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"读取汇率缓存失败: {e}")
        return None


def _set_exchange_rate_cache(payload):
    with EXCHANGE_RATE_LOCK:
        EXCHANGE_RATE_CACHE["ts"] = time.time()
        EXCHANGE_RATE_CACHE["data"] = payload


def refresh_exchange_rate_cache():
    payload = _fetch_exchange_rate_payload()
    _set_exchange_rate_cache(payload)
    _write_exchange_rate_cache(payload)
    return payload


def _get_exchange_rate_cached_payload():
    with EXCHANGE_RATE_LOCK:
        if EXCHANGE_RATE_CACHE["data"]:
            return dict(EXCHANGE_RATE_CACHE["data"])

    payload = _read_exchange_rate_cache_file()
    if payload:
        _set_exchange_rate_cache(payload)
        cached = dict(payload)
        cached["stale"] = True
        return cached
    return None


def exchange_rate_chart():
    payload = _get_exchange_rate_cached_payload()
    if payload:
        return jsonify(payload)

    try:
        payload = refresh_exchange_rate_cache()
        return jsonify(payload)
    except Exception as e:
        return jsonify({"error": "cannot load exchange rate data", "detail": str(e)}), 503


def exchange_rate_refresh_loop():
    while True:
        try:
            refresh_exchange_rate_cache()
        except Exception as e:
            print(f"后台汇率刷新失败: {e}")
        time.sleep(EXCHANGE_RATE_REFRESH_INTERVAL)


def start_exchange_rate_refresher():
    global EXCHANGE_RATE_REFRESH_STARTED
    if EXCHANGE_RATE_REFRESH_STARTED:
        return
    EXCHANGE_RATE_REFRESH_STARTED = True

    cached = _read_exchange_rate_cache_file()
    if cached:
        _set_exchange_rate_cache(cached)

    thread = threading.Thread(target=exchange_rate_refresh_loop, daemon=True)
    thread.start()


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
