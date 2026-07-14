import os
import time
from flask import Blueprint, request, jsonify, render_template

bp = Blueprint("tool_2", __name__)
CLIPBOARD_FILE = "/home/bbdwz/projects/website/data/tools/clipboard.txt"
os.makedirs(os.path.dirname(CLIPBOARD_FILE), exist_ok=True)

# 内存中的状态缓存
clipboard_state = {
    "content": "",
    "timestamp": 0
}

# 初始化：尝试从文件读取
if os.path.exists(CLIPBOARD_FILE):
    try:
        with open(CLIPBOARD_FILE, "r", encoding="utf-8") as f:
            clipboard_state["content"] = f.read()
            clipboard_state["timestamp"] = time.time()
    except:
        pass

@bp.route('/clipboard')
def tool_2_page():
    return render_template('tool_2.html')

@bp.route('/api/clipboard', methods=['GET', 'POST'])
def handle_clipboard():
    global clipboard_state
    
    if request.method == 'POST':
        data = request.json
        new_content = data.get('content', '')
        client_ts = data.get('timestamp', 0)
        
        # 核心逻辑：按时间戳（版本）竞争
        # 如果客户端发送的基准时间戳落后于服务器，说明在此期间已有别的端更新了
        if client_ts < clipboard_state["timestamp"]:
            return jsonify({
                "content": clipboard_state["content"],
                "timestamp": clipboard_state["timestamp"],
                "error": "conflict"
            })
        
        # 只有内容发生变化时才更新
        if new_content != clipboard_state["content"]:
            clipboard_state["content"] = new_content
            clipboard_state["timestamp"] = time.time()
            
            # 持久化到文件
            try:
                with open(CLIPBOARD_FILE, "w", encoding="utf-8") as f:
                    f.write(new_content)
            except Exception as e:
                print(f"Clipboard save error: {e}")
                
        return jsonify({
            "content": clipboard_state["content"],
            "timestamp": clipboard_state["timestamp"]
        })
    
    # GET 请求返回当前内容
    return jsonify({
        "content": clipboard_state["content"],
        "timestamp": clipboard_state["timestamp"]
    })
