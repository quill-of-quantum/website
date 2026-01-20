from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, send_from_directory
)
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from collections import deque
import threading
import sqlite3, psutil, time, datetime, os, requests
import matplotlib.pyplot as plt
from datetime import date, timedelta
import io, base64
import io
import base64
import qrcode
from mijiaAPI.login import mijiaLogin, LoginError

os.environ['TZ'] = 'Europe/Berlin'
time.tzset()

# ===============================
# Flask 初始化
# ===============================
app = Flask(__name__)
app.secret_key = "replace_this_with_a_strong_random_key"  # 请替换为随机长字符串
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 允许最大 500MB 上传

# ===============================
# 导入并注册蓝图
# ===============================
from tracker_api import bp as tracker_bp
app.register_blueprint(tracker_bp)

from tools_api import bp as tools_bp
app.register_blueprint(tools_bp)

from map.map_api import bp as map_bp
app.register_blueprint(map_bp)

from aurora.aurora_api import bp as aurora_bp
app.register_blueprint(aurora_bp)

from letter_league.letter_api import bp as letter_bp
app.register_blueprint(letter_bp)

from game.game_api import bp as game_bp, socketio, start_room_cleaner
app.register_blueprint(game_bp)
socketio.init_app(app)
start_room_cleaner()
# ===============================
# 基础配置
# ===============================
DB_PATH = "/home/bbdwz/projects/website/tracker.db"
ADMIN_PREFIX = "/1"
AUTH_PATH = os.path.expanduser("~/.config/mijia-api/mijia-api-auth.json") #米家token存储路径

# 模拟一个简单的“用户数据库”
USER_DB = {
    "admin": generate_password_hash("bbdwz")
}

# ===============================
# ----------- 普通区 ------------
# ===============================

@app.route("/")
def index():
    """主页"""
    return render_template("index.html")

@app.route("/tools")
def tools():
    """工具页"""
    return render_template("tools.html")


@app.route("/tracker")
def tracker_ui():
    """追踪页"""
    return render_template("tracker.html")

@app.route("/viewer")
def viewer():
    """3D 模型调试页面"""
    return render_template("viewer.html")

@app.route("/map")
def map_ui():
    """路线规划页"""
    return render_template("map.html")

@app.route("/api/exchange_rate")
def exchange_rate_chart():
    """
    Get latest USD→CNY & EUR→CNY using open.er-api.com,
    plus recent ECB history for chart background.
    """
    import requests, io, base64
    from datetime import datetime
    import matplotlib.pyplot as plt
    import xml.etree.ElementTree as ET

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
        # offline fallback
        plt.figure(figsize=(7, 4))
        plt.text(0.5, 0.5, "⚠️ Cannot load ECB data", ha="center", va="center")
        plt.axis("off")
        buf = io.BytesIO(); plt.savefig(buf, format="png"); buf.seek(0)
        img_b64 = base64.b64encode(buf.read()).decode(); plt.close()
        return jsonify({"image": f"data:image/png;base64,{img_b64}"})

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

    # ---------- Step 3: plot ----------
    fig, axes = plt.subplots(2, 1, figsize=(8, 7), sharex=True)

    axes[0].plot(dates, eur_cny, color="tab:blue")
    axes[0].set_title("EUR to CNY (Past 90 Days)")
    axes[0].grid(True, ls="--", alpha=0.4)

    axes[1].plot(dates, usd_cny, color="tab:green")
    axes[1].set_title("USD to CNY (Past 90 Days)")
    axes[1].grid(True, ls="--", alpha=0.4)

    for ax in axes:
        ax.tick_params(axis="x", labelrotation=45)
        ax.xaxis.set_major_locator(plt.MaxNLocator(8))

    if latest_eur and latest_usd:
        fig.suptitle(
            f"Latest ({latest_date}) EUR→CNY = {latest_eur:.3f} | USD→CNY = {latest_usd:.3f}",
            fontsize=11, y=0.02
        )
    plt.tight_layout(rect=[0, 0.05, 1, 0.96])

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=120)
    buf.seek(0)
    img_b64 = base64.b64encode(buf.read()).decode()
    plt.close(fig)

    return jsonify({
        "image": f"data:image/png;base64,{img_b64}",
        "latest_date": latest_date,
        "EUR_to_CNY": latest_eur,
        "USD_to_CNY": latest_usd
    })

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
            return jsonify({"error": "缺少时间或数值"}), 400

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

        return jsonify({"status": "ok", "reply": msg})

    # ===== 动作2：获取最新读数 =====
    elif action == "get_latest":
        txt_path = "/home/bbdwz/projects/website/weather/number.txt"
        if not os.path.exists(txt_path):
            return jsonify({"error": "暂无数据"}), 404

        lines = [line.strip() for line in open(txt_path, encoding="utf-8") if line.strip()]
        if len(lines) < 2:
            return jsonify({"error": "数据不足"}), 400

        t, v = lines[-2], lines[-1]
        return jsonify({"status": "ok", "time": t, "value": v})

    # ===== 其他未知动作 =====
    else:
        return jsonify({"error": f"未知动作: {action}"}), 400

# ===============================
# ----------- 管理区 ------------
# ===============================

import ipaddress

LAN_NETWORKS = [
    ipaddress.ip_network("192.168.178.0/24")
]

def is_lan_ip(ip):
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in LAN_NETWORKS)
    except:
        return False

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith(ADMIN_PREFIX + "/api") or request.path.startswith("/api/"):
                return jsonify({"require_login": True}), 403
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper

@app.route(ADMIN_PREFIX + "/api/mijia_qr")
@login_required
def mijia_qr():
    """
    返回米家扫码登录的二维码图片。
    """

    login = mijiaLogin(save_path=AUTH_PATH)

    try:
        # QRlogin() 会返回一个 dict，其中 loginUrl 用于扫码
        info = login.QRlogin(only_get_qr=True)

        qr_url = info["qr"]     # 例如：mijia://xxx
        login_token = info["token"]  # 登录会话 ID（可选）

        # 生成二维码
        qr = qrcode.make(qr_url)
        buf = io.BytesIO()
        qr.save(buf, format="PNG")
        buf.seek(0)

        img_b64 = base64.b64encode(buf.read()).decode()

        return jsonify({
            "status": "ok",
            "qr": "data:image/png;base64," + img_b64,
            "token": login_token
        })

    except LoginError as e:
        return jsonify({"status": "error", "msg": str(e)}), 500

@app.route(ADMIN_PREFIX + "/logout")
@login_required
def admin_logout():
    """管理员退出"""
    session.clear()
    return redirect(url_for("index"))

@app.route(ADMIN_PREFIX + "/")
@login_required
def admin_dashboard():
    """管理主页"""
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent
    return render_template("admin_index.html", user=session.get("user"), cpu=cpu, mem=mem)

@app.route(ADMIN_PREFIX + "/api/command", methods=["POST"])
@login_required
def admin_command():
    """管理员接口：记录命令日志"""
    data = request.json or {}
    cmd = data.get("cmd", "")
    with open("/home/bbdwz/admin_commands.log", "a") as f:
        f.write(f"[{time.ctime()}] {cmd}\n")
    return jsonify({"status": "ok", "received": cmd})

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
        return jsonify({"status": "success", "user": username})
    else:
        return jsonify({"status": "error", "message": "用户名或密码错误"}), 401

@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    """API退出接口"""
    session.clear()
    return jsonify({"status": "success"})

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
