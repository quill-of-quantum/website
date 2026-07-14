from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, send_from_directory, g
)
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

from modules.index.api import collect_system_info, register_routes as register_index_routes
register_index_routes(app)

# 访问日志配置
VISITER_LOG_PATH = "/home/bbdwz/projects/website/logs/visiter.log"
os.makedirs(os.path.dirname(VISITER_LOG_PATH), exist_ok=True)

# ===============================
# 导入并注册蓝图
# ===============================
from modules.tracker.api import bp as tracker_bp
app.register_blueprint(tracker_bp)

from modules.cloud.api import bp as cloud_bp
app.register_blueprint(cloud_bp)

from modules.auth.api import bp as auth_bp
app.register_blueprint(auth_bp)

from modules.shortcut.api import bp as shortcut_bp
app.register_blueprint(shortcut_bp)

from modules.admin.api import bp as admin_bp
from modules.admin.api import record_visit, record_request_timing, is_lan_ip
app.register_blueprint(admin_bp)

from modules.map.api import bp as map_bp
app.register_blueprint(map_bp)

from modules.route_creator.api import bp as route_creator_bp
app.register_blueprint(route_creator_bp)

from modules.aurora.api import bp as aurora_bp
app.register_blueprint(aurora_bp)

from modules.letter_league.api import bp as letter_bp
app.register_blueprint(letter_bp)

from modules.game.api import bp as game_bp, socketio, start_room_cleaner
app.register_blueprint(game_bp)
socketio.init_app(app)
start_room_cleaner()

from modules.sensor.api import bp as sensor_bp, start_sensor_logger
app.register_blueprint(sensor_bp)
start_sensor_logger()

from modules.tools.vision import bp as vision_bp
app.register_blueprint(vision_bp)

from modules.tools.clipboard import bp as clipboard_bp
app.register_blueprint(clipboard_bp)

from modules.situation.api import bp as situation_bp
app.register_blueprint(situation_bp)

from modules.garden.api import bp as garden_bp
app.register_blueprint(garden_bp)

from modules.mail.api import bp as email_bp
app.register_blueprint(email_bp)
# ===============================
# 基础配置
# ===============================
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
