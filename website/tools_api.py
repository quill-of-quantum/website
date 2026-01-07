from flask import Blueprint, request, jsonify, send_from_directory, session
import os
import shutil
import time

bp = Blueprint("tools", __name__)
UPLOAD_FOLDER = "/home/bbdwz/projects/website/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# 上传文件（仅保存）
@bp.route("/api/tools/upload", methods=["POST"])
def upload_file():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "未接收到文件"}), 400

    filename = file.filename
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(save_path)

    message = f"✅ 文件已上传：{filename}"
    print(f"[UPLOAD] 文件已保存: {save_path}")

    return jsonify({
        "ok": True,
        "message": message,
        "filename": filename
    })


# 列出文件 + 存储空间信息
@bp.route("/api/tools/files")
def list_files():
    sort_by = request.args.get("sort", "time")  # time 或 name
    order = request.args.get("order", "desc")   # asc 或 desc
    
    files = []
    for f in os.listdir(UPLOAD_FOLDER):
        path = os.path.join(UPLOAD_FOLDER, f)
        if os.path.isfile(path):
            size_mb = os.path.getsize(path) / (1024 * 1024)
            create_time = os.path.getctime(path)
            
            # 获取文件扩展名用于分类排序
            ext = os.path.splitext(f)[1].lower()
            
            files.append({
                "name": f,
                "size": f"{size_mb:.2f} MB",
                "create_time": create_time,
                "ext": ext
            })
    
    # 排序逻辑
    if sort_by == "time":
        files.sort(key=lambda x: x["create_time"], reverse=(order == "desc"))
    elif sort_by == "name":
        # 先按扩展名排序，再按文件名排序
        files.sort(key=lambda x: (x["ext"], x["name"].lower()), reverse=(order == "desc"))
    
    # 计算磁盘使用情况
    total, used, free = shutil.disk_usage(UPLOAD_FOLDER)
    info = {
        "total": f"{total / (1024**3):.2f} GB",
        "used": f"{used / (1024**3):.2f} GB",
        "free": f"{free / (1024**3):.2f} GB"
    }

    return jsonify({
        "files": files,
        "disk": info
    })


# 删除文件
@bp.route("/api/tools/delete/<path:name>", methods=["POST"])
def delete_file(name):
    """删除文件 - 需要登录"""
    # 检查登录状态
    if not session.get("logged_in"):
        return jsonify({"error": "需要登录后才能删除文件", "require_login": True}), 403
    
    try:
        os.remove(os.path.join(UPLOAD_FOLDER, name))
        return jsonify({"message": f"🗑️ 已删除 {name}"})
    except Exception as e:
        return jsonify({"message": f"删除失败: {e}"}), 500


# 文件下载
@bp.route("/api/tools/download/<path:filename>")
def download_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


# 文件访问（直接访问）
@bp.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)
