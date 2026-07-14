import os
import subprocess
import time

from flask import Blueprint, jsonify, request

from modules.situation.api import record_situation_event


bp = Blueprint("shortcut", __name__)


@bp.route("/api/shortcut/run", methods=["POST"])
def shortcut_run():
    data = request.get_json(force=True)
    action = data.get("action")

    log_path = "/home/bbdwz/projects/website/shortcut.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 收到: {data}\n")

    if action == "append_reading":
        t = data.get("time")
        v = data.get("value")
        if not (t and v):
            return jsonify({
                "success": False,
                "error": "缺少时间或数值",
                "code": "MISSING_TIME_OR_VALUE",
            }), 400

        txt_path = "/home/bbdwz/projects/website/data/weather/number.txt"
        os.makedirs(os.path.dirname(txt_path), exist_ok=True)
        with open(txt_path, "a", encoding="utf-8") as f:
            f.write(f"{t}\n{v}\n")
            f.flush()
            os.fsync(f.fileno())

        py_env = "/home/bbdwz/miniconda3/envs/web/bin/python"
        analyze_script = "/home/bbdwz/projects/website/modules/weather/analyze.py"

        try:
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
                "value": v,
            },
        })

    if action == "get_latest":
        txt_path = "/home/bbdwz/projects/website/data/weather/number.txt"
        if not os.path.exists(txt_path):
            return jsonify({
                "success": False,
                "error": "暂无数据",
                "code": "NO_DATA",
            }), 404

        lines = [line.strip() for line in open(txt_path, encoding="utf-8") if line.strip()]
        if len(lines) < 2:
            return jsonify({
                "success": False,
                "error": "数据不足",
                "code": "INSUFFICIENT_DATA",
            }), 400

        t, v = lines[-2], lines[-1]
        return jsonify({
            "success": True,
            "message": "已获取最新读数",
            "data": {
                "time": t,
                "value": v,
            },
        })

    if action == "situation":
        event, error = record_situation_event(data)
        if error:
            message, status_code = error
            return jsonify({
                "success": False,
                "error": message,
                "code": "SITUATION_RECORD_FAILED",
            }), status_code
        message = f"✅ 已记录状态：{event['event']} / {event['net']} @ {event['time']}"
        return jsonify({
            "success": True,
            "message": message,
            "data": event,
        })

    return jsonify({
        "success": False,
        "error": f"未知动作: {action}",
        "code": "UNKNOWN_ACTION",
    }), 400
