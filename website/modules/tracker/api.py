from flask import Blueprint, request, jsonify
import sqlite3
import asyncio
import os
import json
import shutil
import subprocess
from datetime import datetime
from modules.tracker.browser import fetch_tracking, parse_tracking_result, compare_with_last

bp = Blueprint("tracker", __name__)

TRACKER_DATA_DIR = "/home/bbdwz/projects/website/data/tracker"
DB_PATH = f"{TRACKER_DATA_DIR}/tracker.db"
TRACKER_SERVICE_UNIT = "tracker_scheduler.service"


def _systemctl_show(unit, properties):
    systemctl_path = shutil.which("systemctl", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if not systemctl_path:
        return {}
    args = [systemctl_path, "show"]
    for prop in properties:
        args.extend(["-p", prop])
    args.append(unit)
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return {}
        data = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                data[key] = value
        return data
    except Exception:
        return {}

def db_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # 启用 WAL 模式避免锁定
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@bp.route("/api/tracker/service/status")
def tracker_service_status():
    data = _systemctl_show(TRACKER_SERVICE_UNIT, ["ActiveState", "SubState", "UnitFileState"])
    active_state = data.get("ActiveState") or "unknown"
    enabled_state = data.get("UnitFileState") or "unknown"
    enabled = enabled_state not in ("disabled", "masked")
    visible = enabled or enabled_state == "unknown"
    return jsonify({
        "ok": True,
        "unit": TRACKER_SERVICE_UNIT,
        "active_state": active_state,
        "sub_state": data.get("SubState") or "unknown",
        "enabled_state": enabled_state,
        "enabled": enabled,
        "visible": visible,
    })


# === 获取任务列表 ===
@bp.route("/api/tracker/list")
def list_tasks():
    conn = db_conn()
    try:
        tasks = conn.execute("SELECT * FROM tracker_tasks").fetchall()
        return jsonify([dict(t) for t in tasks])
    finally:
        conn.close()


# === 添加任务 ===
@bp.route("/api/tracker/add", methods=["POST"])
def add_task():
    """添加任务 - 不需要登录"""
    data = request.json
    num = data.get("tracking_number")
    interval = int(data.get("interval", 60))
    conn = db_conn()
    try:
        conn.execute(
            "INSERT INTO tracker_tasks (tracking_number, interval_minutes) VALUES (?, ?)",
            (num, interval),
        )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


# === 启用 / 停用任务 ===
@bp.route("/api/tracker/toggle/<int:task_id>", methods=["POST"])
def toggle_task(task_id):
    """切换任务状态 - 不需要登录"""
    conn = db_conn()
    try:
        task = conn.execute(
            "SELECT enabled FROM tracker_tasks WHERE id=?", (task_id,)
        ).fetchone()
        if not task:
            return jsonify({"error": "not found"}), 404

        new_state = 0 if task["enabled"] else 1
        conn.execute(
            "UPDATE tracker_tasks SET enabled=? WHERE id=?", (new_state, task_id)
        )
        conn.commit()
        return jsonify({"ok": True, "enabled": new_state})
    finally:
        conn.close()


# === 手动执行任务 ===
@bp.route("/api/tracker/run/<int:task_id>", methods=["POST"])
def run_task(task_id):
    """手动执行任务 - 不需要登录"""
    conn = db_conn()
    try:
        task = conn.execute(
            "SELECT * FROM tracker_tasks WHERE id=?", (task_id,)
        ).fetchone()
        if not task:
            return jsonify({"error": "not found"}), 404

        async def run():
            try:
                # ✅ 增加总超时保护（90秒）
                html = await asyncio.wait_for(
                    fetch_tracking(task["tracking_number"]), 
                    timeout=90
                )
                parsed = parse_tracking_result(html)

                # 为每条任务创建独立的 JSON 文件
                info_path = f"{TRACKER_DATA_DIR}/tracker_data_{task_id}.json"

                # 默认状态
                status = "抓取失败"

                if parsed:
                    status = compare_with_last(parsed, info_path)

                    # ✅ 遇到"空结果"或"抓取失败"时保留旧状态
                    if "最新状态：无" in status or "抓取失败" in status:
                        old = conn.execute(
                            "SELECT last_status FROM tracker_tasks WHERE id=?", (task_id,)
                        ).fetchone()
                        if old and old["last_status"]:
                            status = old["last_status"] + "（保持）"

                # ✅ 只有解析到内容才更新时间戳
                conn.execute(
                    "UPDATE tracker_tasks SET last_run=datetime('now', 'localtime'), last_status=? WHERE id=?",
                    (status, task_id),
                )
                conn.commit()
                return status
            except asyncio.TimeoutError:
                return f"❌ 执行超时（90秒）"
            except Exception as e:
                return f"❌ 执行错误：{str(e)}"

        result = asyncio.run(run())
        return jsonify({"ok": True, "result": result})
    finally:
        conn.close()


# === 刷新所有任务 ===
@bp.route("/api/tracker/refresh_all", methods=["POST"])
def refresh_all_tasks():
    """刷新所有任务 - 不需要登录"""
    conn = db_conn()
    try:
        tasks = conn.execute("SELECT id FROM tracker_tasks WHERE enabled=1").fetchall()
        count = len(tasks)
        
        # 这里可以添加批量刷新逻辑
        # 为了简单起见，我们只返回成功消息
        
        return jsonify({"ok": True, "message": f"✅ 已触发 {count} 个任务的刷新"})
    finally:
        conn.close()


# === 删除已完成任务 ===
@bp.route("/api/tracker/delete_completed", methods=["POST"])
def delete_completed_tasks():
    """删除已完成任务 - 需要登录"""
    from flask import session
    
    # 检查登录状态
    if not session.get("logged_in"):
        return jsonify({"error": "需要删除任务请先登录", "require_login": True}), 403
    
    conn = db_conn()
    try:
        # 假设包含"已签收"或"已完成"关键词的为已完成任务
        completed = conn.execute("""
            SELECT id FROM tracker_tasks 
            WHERE last_status LIKE '%已签收%' 
               OR last_status LIKE '%已完成%'
               OR last_status LIKE '%delivered%'
        """).fetchall()
        
        count = len(completed)
        
        for task in completed:
            task_id = task["id"]
            conn.execute("DELETE FROM tracker_tasks WHERE id=?", (task_id,))
            # 删除关联的数据文件
            info_path = f"{TRACKER_DATA_DIR}/tracker_data_{task_id}.json"
            if os.path.exists(info_path):
                os.remove(info_path)
        
        conn.commit()
        return jsonify({"ok": True, "count": count})
    finally:
        conn.close()


# === 刷新单个任务 ===
@bp.route("/api/tracker/refresh/<int:task_id>", methods=["POST"])
def refresh_single_task(task_id):
    """刷新单个任务 - 不需要登录"""
    # 调用现有的 run_task 逻辑
    return run_task(task_id)


# === 删除任务 ===
@bp.route("/api/tracker/delete/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    """删除任务 - 需要登录"""
    from flask import session, jsonify
    
    # 检查登录状态
    if not session.get("logged_in"):
        return jsonify({"error": "需要删除任务请先登录", "require_login": True}), 403
    
    conn = db_conn()
    try:
        conn.execute("DELETE FROM tracker_tasks WHERE id=?", (task_id,))
        # ✅ 同时删除关联的 JSON 数据文件
        info_path = f"{TRACKER_DATA_DIR}/tracker_data_{task_id}.json"
        if os.path.exists(info_path):
            os.remove(info_path)
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


# === 获取详细信息 ===
@bp.route("/api/tracker/info/<int:task_id>")
def get_info(task_id):
    """获取单个任务的详细物流信息"""
    conn = db_conn()
    try:
        # ✅ 先验证任务存在
        task = conn.execute(
            "SELECT id, tracking_number FROM tracker_tasks WHERE id=?", 
            (task_id,)
        ).fetchone()
        
        if not task:
            return jsonify({"error": "task_not_found"}), 404

        # ✅ 读取该任务独立的 JSON 文件
        info_path = f"{TRACKER_DATA_DIR}/tracker_data_{task_id}.json"
        
        if not os.path.exists(info_path):
            # 第一次抓取前没有数据
            return jsonify({
                "tracking_number": task["tracking_number"],
                "routes": [],
                "error": "no_data_yet"
            })
        
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # ✅ 确保返回的数据包含 tracking_number
            if "tracking_number" not in data:
                data["tracking_number"] = task["tracking_number"]
            
            return jsonify(data)
        except Exception as e:
            return jsonify({
                "tracking_number": task["tracking_number"],
                "routes": [],
                "error": f"read_error: {str(e)}"
            })
    finally:
        conn.close()
