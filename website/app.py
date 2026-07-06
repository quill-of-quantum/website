from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, send_from_directory, g
)
from werkzeug.security import generate_password_hash, check_password_hash
from collections import deque
import threading
import sqlite3, psutil, time, datetime, os, requests
import matplotlib.pyplot as plt
from datetime import date, timedelta
import io, base64
import io
import base64

os.environ['TZ'] = 'Europe/Berlin'
time.tzset()

# ===============================
# Flask 初始化
# ===============================
app = Flask(__name__)
app.secret_key = "replace_this_with_a_strong_random_key"  # 请替换为随机长字符串
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 允许最大 500MB 上传

# 👇 新增：引入并开启全局 GZIP 压缩
from flask_compress import Compress
compress = Compress()
compress.init_app(app)

# 访问日志配置
VISITER_LOG_PATH = "/home/bbdwz/projects/website/logs/visiter.log"
os.makedirs(os.path.dirname(VISITER_LOG_PATH), exist_ok=True)

# ===============================
# 导入并注册蓝图
# ===============================
from tracker_api import bp as tracker_bp
app.register_blueprint(tracker_bp)

from cloud_api import bp as cloud_bp
app.register_blueprint(cloud_bp)

from admin_api import bp as admin_bp
from admin_api import record_visit, record_request_timing, is_lan_ip
app.register_blueprint(admin_bp)

from map.map_api import bp as map_bp
app.register_blueprint(map_bp)

from route_creator.route_creator_api import bp as route_creator_bp
app.register_blueprint(route_creator_bp)

from aurora.aurora_api import bp as aurora_bp
app.register_blueprint(aurora_bp)

from letter_league.letter_api import bp as letter_bp
app.register_blueprint(letter_bp)

from game.game_api import bp as game_bp, socketio, start_room_cleaner
app.register_blueprint(game_bp)
socketio.init_app(app)
start_room_cleaner()

from sensor.sensor_api import bp as sensor_bp, start_sensor_logger
app.register_blueprint(sensor_bp)
start_sensor_logger()

from tools.tool_1 import bp as vision_bp
app.register_blueprint(vision_bp)

from tools.tool_2 import bp as clipboard_bp
app.register_blueprint(clipboard_bp)

from situation.situation_api import bp as situation_bp
from situation.situation_api import record_situation_event
app.register_blueprint(situation_bp)

from garden.garden_api import bp as garden_bp
app.register_blueprint(garden_bp)
# ===============================
# 基础配置
# ===============================
DB_PATH = "/home/bbdwz/projects/website/tracker.db"
# 模拟一个简单的“用户数据库”
USER_DB = {
    "admin": generate_password_hash("bbdwz")
}

# 汇率接口缓存（10分钟）
EXCHANGE_RATE_CACHE_TTL = 600
EXCHANGE_RATE_CACHE = {
    "ts": 0,
    "data": None
}

# ===============================
# ----------- 普通区 ------------
# ===============================

@app.before_request
def track_visit():
    g.request_start = time.time()
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip and "," in ip:
        ip = ip.split(",", 1)[0].strip()
    path = request.path
    if path.startswith("/static/") or path.startswith("/uploads/") or path.startswith("/thumbnails/"):
        return
    if path.startswith("/1/api/status"):
        return
    if path.startswith("/api/") or "/api/" in path:
        return
    if "." in os.path.basename(path):
        return
    record_visit(ip or "unknown", path)
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        logged_in = "已登录" if session.get("logged_in") else "未登录"
        operation = request.method
        user_agent = request.headers.get("User-Agent", "unknown")
        line = (
            f"时间:{ts}\tIP:{ip or 'unknown'}\t路径:{path}\t登录:{logged_in}"
            f"\t操作:{operation}\t设备:{user_agent}\n"
        )
        with open(VISITER_LOG_PATH, "a") as f:
            f.write(line)
    except Exception as e:
        print(f"写入访问日志失败: {e}")

@app.after_request
def record_latency(response):
    start = getattr(g, "request_start", None)
    if start is not None:
        duration_ms = (time.time() - start) * 1000
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        if ip and "," in ip:
            ip = ip.split(",", 1)[0].strip()
        record_request_timing(duration_ms, is_lan=is_lan_ip(ip or ""))
    return response

@app.route("/")
def index():
    """主页"""
    return render_template("index.html")

@app.route("/cloud")
def cloud():
    """云盘页"""
    return render_template("cloud.html")


@app.route("/tracker")
def tracker_ui():
    """追踪页"""
    return render_template("tracker.html")

@app.route("/vision")
def vision_ui():
    """智能视觉检测页"""
    return render_template("tool_1.html")

@app.route("/viewer")
def viewer():
    """3D 模型调试页面"""
    return render_template("viewer.html")

@app.route("/map")
def map_ui():
    """路线规划页"""
    return render_template("map.html")

@app.route("/route_creator")
def route_creator_ui():
    """路线创作页"""
    return render_template("route_creator.html")

@app.route("/api/exchange_rate")
def exchange_rate_chart():
    """
    Get latest USD→CNY & EUR→CNY using open.er-api.com,
    plus recent ECB history for chart background.
    """
    import requests
    from datetime import datetime
    import xml.etree.ElementTree as ET
    now_ts = time.time()
    if EXCHANGE_RATE_CACHE["data"] and (now_ts - EXCHANGE_RATE_CACHE["ts"] < EXCHANGE_RATE_CACHE_TTL):
        return jsonify(EXCHANGE_RATE_CACHE["data"])

    # ---------- Step 1: latest spot rates ----------
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

    # ---------- Step 2: historical (ECB 90 days) ----------
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
            "pct_change": round(pct_change, 2)
        }

    payload = {
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
        "eur_cny": eur_cny,
        "usd_cny": usd_cny,
        "stats": {
            "eur": build_stats(eur_cny, dates),
            "usd": build_stats(usd_cny, dates)
        },
        "latest_date": latest_date,
        "latest_eur": latest_eur,
        "latest_usd": latest_usd
    }
    EXCHANGE_RATE_CACHE["ts"] = now_ts
    EXCHANGE_RATE_CACHE["data"] = payload
    return jsonify(payload)

# ===============================
# 系统数据缓存（内存存储最近30个数据点）
# ===============================
SYSINFO_CACHE = {
    "timestamps": deque(maxlen=30),
    "cpu": deque(maxlen=30),
    "memory": deque(maxlen=30),
    "temperature": deque(maxlen=30)
}

def collect_system_info():
    """后台线程：定期采集系统信息"""
    import subprocess, psutil, re
    
    while True:
        try:
            data = {}
            
            # CPU & Memory
            data["cpu_percent"] = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            data["memory_percent"] = mem.percent
            data["memory_used"] = round(mem.used / (1024 ** 2), 1)
            data["memory_total"] = round(mem.total / (1024 ** 2), 1)
            
            # 温度
            try:
                with open("/sys/class/thermal/thermal_zone0/temp") as f:
                    data["temperature"] = round(int(f.read()) / 1000.0, 1)
            except FileNotFoundError:
                data["temperature"] = None
            
            # WiFi 信号
            try:
                iw_output = subprocess.check_output("iwconfig", shell=True, text=True)
                wlan = re.search(r"^(\w+)\s+IEEE", iw_output, re.M)
                if wlan:
                    iface = wlan.group(1)
                    line = [l for l in iw_output.split("\n") if "Signal level" in l]
                    if line:
                        data["wifi_signal"] = line[0].split("Signal level=")[-1].split()[0]
            except Exception:
                data["wifi_signal"] = None
            
            # 运行时间
            uptime_seconds = time.time() - psutil.boot_time()
            data["uptime"] = str(datetime.timedelta(seconds=int(uptime_seconds)))
            
            # 保存到缓存
            timestamp = datetime.now().strftime("%H:%M:%S")
            SYSINFO_CACHE["timestamps"].append(timestamp)
            SYSINFO_CACHE["cpu"].append(data["cpu_percent"])
            SYSINFO_CACHE["memory"].append(data["memory_percent"])
            SYSINFO_CACHE["temperature"].append(data["temperature"])
            
            time.sleep(3)
        except Exception as e:
            print(f"⚠️ 采集系统信息出错: {e}")
            time.sleep(3)

@app.route("/api/system")
def system_info():
    """系统状态 API - 返回当前值和历史数据"""
    import subprocess, psutil, re
    
    data = {}
    
    # 获取当前值
    data["cpu_percent"] = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    data["memory_percent"] = mem.percent
    data["memory_used"] = round(mem.used / (1024 ** 2), 1)
    data["memory_total"] = round(mem.total / (1024 ** 2), 1)
    
    # 温度
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            data["temperature"] = round(int(f.read()) / 1000.0, 1)
    except FileNotFoundError:
        data["temperature"] = None
    
    # WiFi 信号
    try:
        iw_output = subprocess.check_output("iwconfig", shell=True, text=True)
        wlan = re.search(r"^(\w+)\s+IEEE", iw_output, re.M)
        if wlan:
            iface = wlan.group(1)
            line = [l for l in iw_output.split("\n") if "Signal level" in l]
            if line:
                data["wifi_signal"] = line[0].split("Signal level=")[-1].split()[0]
    except Exception:
        data["wifi_signal"] = None
    
    # 运行时间
    uptime_seconds = time.time() - psutil.boot_time()
    data["uptime"] = str(datetime.timedelta(seconds=int(uptime_seconds)))
    
    # 添加历史数据
    data["history"] = {
        "timestamps": list(SYSINFO_CACHE["timestamps"]),
        "cpu": list(SYSINFO_CACHE["cpu"]),
        "memory": list(SYSINFO_CACHE["memory"]),
        "temperature": list(SYSINFO_CACHE["temperature"])
    }
    
    return jsonify(data)

@app.route("/weather_chart/<path:filename>")
def weather_chart_file(filename):
    return send_from_directory("/home/bbdwz/projects/website/weather", filename)

from flask import Response

@app.route("/weather/<path:filename>")
def serve_weather_file(filename):
    full_path = os.path.join("/home/bbdwz/projects/website/weather", filename)
    if not os.path.exists(full_path):
        return "File not found", 404
    # 对 SVG 显式声明 MIME 类型
    if filename.endswith(".svg"):
        with open(full_path, "r", encoding="utf-8") as f:
            return Response(f.read(), mimetype="image/svg+xml")
    return send_from_directory("/home/bbdwz/projects/website/weather", filename)

@app.route("/api/shortcut/run", methods=["POST"])
def shortcut_run():
    import subprocess, json, os, time
    data = request.get_json(force=True)
    action = data.get("action")

    log_path = "/home/bbdwz/projects/website/shortcut.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 收到: {data}\n")

    # ===== 动作1：追加暖气读数 =====
    if action == "append_reading":
        t = data.get("time")
        v = data.get("value")
        if not (t and v):
            return jsonify({
                "success": False,
                "error": "缺少时间或数值",
                "code": "MISSING_TIME_OR_VALUE"
            }), 400

        txt_path = "/home/bbdwz/projects/website/weather/number.txt"
        os.makedirs(os.path.dirname(txt_path), exist_ok=True)
        with open(txt_path, "a", encoding="utf-8") as f:
            f.write(f"{t}\n{v}\n")
            f.flush()
            os.fsync(f.fileno())  # 确保数据立即落盘

        # 环境与脚本路径
        py_env = "/home/bbdwz/miniconda3/envs/web/bin/python"
        base_dir = "/home/bbdwz/projects/website/weather"
        analyze_script = os.path.join(base_dir, "analyze_weather.py")

        try:
            # 只运行 analyze_weather.py（生成图表）
            subprocess.Popen([py_env, analyze_script])
            msg = f"✅ 已记录读数 {v} 于 {t}。\n→ 已启动 analyze_weather.py 更新图表。"

        except Exception as e:
            msg = f"⚠️ 已记录读数 {v} 于 {t}，但分析脚本执行失败：{e}"

        return jsonify({
            "success": True,
            "message": msg,
            "data": {
                "action": action,
                "time": t,
                "value": v
            }
        })

    # ===== 动作2：获取最新读数 =====
    elif action == "get_latest":
        txt_path = "/home/bbdwz/projects/website/weather/number.txt"
        if not os.path.exists(txt_path):
            return jsonify({
                "success": False,
                "error": "暂无数据",
                "code": "NO_DATA"
            }), 404

        lines = [line.strip() for line in open(txt_path, encoding="utf-8") if line.strip()]
        if len(lines) < 2:
            return jsonify({
                "success": False,
                "error": "数据不足",
                "code": "INSUFFICIENT_DATA"
            }), 400

        t, v = lines[-2], lines[-1]
        return jsonify({
            "success": True,
            "message": "已获取最新读数",
            "data": {
                "time": t,
                "value": v
            }
        })

    # ===== 动作3：记录状态 =====
    elif action == "situation":
        event, error = record_situation_event(data)
        if error:
            message, status_code = error
            return jsonify({
                "success": False,
                "error": message,
                "code": "SITUATION_RECORD_FAILED"
            }), status_code
        message = f"✅ 已记录状态：{event['event']} / {event['net']} @ {event['time']}"
        return jsonify({
            "success": True,
            "message": message,
            "data": event
        })

    # ===== 其他未知动作 =====
    else:
        return jsonify({
            "success": False,
            "error": f"未知动作: {action}",
            "code": "UNKNOWN_ACTION"
        }), 400

# ===============================
# Add a new API endpoint to check login status
@app.route("/api/auth/status")
def auth_status():
    """检查登录状态"""
    return jsonify({
        "logged_in": session.get("logged_in", False),
        "user": session.get("user") if session.get("logged_in") else None
    })

# Add a new API endpoint for frontend login
@app.route("/api/auth/login", methods=["POST"])
def api_login():
    """API登录接口"""
    data = request.get_json() or {}
    username = data.get("username")
    password = data.get("password")
    
    user_hash = USER_DB.get(username)
    if user_hash and check_password_hash(user_hash, password):
        session["logged_in"] = True
        session["user"] = username
        session["login_time"] = int(time.time())
        return jsonify({"success": True, "status": "success", "user": username})
    else:
        return jsonify({"success": False, "status": "error", "message": "用户名或密码错误"}), 401

@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    """API退出接口"""
    session.clear()
    return jsonify({"success": True, "status": "success"})

@app.route("/static/<path:filename>")
def static_files(filename):
    """静态文件服务"""
    return send_from_directory(os.path.join(app.root_path, "static"), filename)

# ===============================
# 启动入口（仅调试用）
# ===============================
if __name__ == "__main__":
    # 启动后台采集线程
    collect_thread = threading.Thread(target=collect_system_info, daemon=True)
    collect_thread.start()

    socketio.run(app, host="0.0.0.0", port=5000)
