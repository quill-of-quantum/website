from flask import Blueprint, request, jsonify, send_from_directory, session
import os
import shutil
import time
from PIL import Image
import io
import json
import uuid

bp = Blueprint("cloud", __name__)
UPLOAD_FOLDER = "/home/bbdwz/projects/website/uploads"
THUMBNAIL_FOLDER = "/home/bbdwz/projects/website/thumbnails"
UPLOAD_META_PATH = os.path.join(UPLOAD_FOLDER, ".meta.json")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMBNAIL_FOLDER, exist_ok=True)

def _load_meta():
    if not os.path.exists(UPLOAD_META_PATH):
        return {}
    try:
        with open(UPLOAD_META_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_meta(meta):
    tmp_path = f"{UPLOAD_META_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp_path, UPLOAD_META_PATH)

def _is_allowed_extension(filename):
    return True

def _make_storage_name(original_filename):
    ext = os.path.splitext(original_filename)[1].lower()
    return f"{uuid.uuid4().hex}{ext}"

def _resolve_stored_name(name, meta):
    candidate = os.path.join(UPLOAD_FOLDER, name)
    if os.path.exists(candidate):
        return name
    matches = [stored for stored, info in meta.items()
               if info.get("original_name") == name]
    if len(matches) == 1:
        return matches[0]
    return None

# 判断是否为图片文件
def is_image_file(filename):
    return filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'))

# 生成缩略图
def generate_thumbnail(file_path, filename):
    try:
        if not is_image_file(filename):
            return False
        
        # 打开原图
        with Image.open(file_path) as img:
            # 转换为RGB模式（处理RGBA等格式）
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            
            # 计算缩略图尺寸（最大120x120，保持比例）
            img.thumbnail((120, 120), Image.Resampling.LANCZOS)
            
            # 保存缩略图
            thumb_name = f"thumb_{filename}"
            thumb_path = os.path.join(THUMBNAIL_FOLDER, thumb_name)
            img.save(thumb_path, 'JPEG', quality=80, optimize=True)
            return True
    except Exception as e:
        print(f"生成缩略图失败: {e}")
        return False

# 清理多余缩略图（无原图对应）
def cleanup_orphan_thumbnails():
    removed = []
    try:
        for fname in os.listdir(THUMBNAIL_FOLDER):
            thumb_path = os.path.join(THUMBNAIL_FOLDER, fname)
            if not os.path.isfile(thumb_path):
                continue
            if not fname.startswith("thumb_"):
                continue
            original_name = fname[len("thumb_"):]
            original_path = os.path.join(UPLOAD_FOLDER, original_name)
            if not os.path.exists(original_path):
                os.remove(thumb_path)
                removed.append(fname)
    except Exception as e:
        print(f"清理缩略图失败: {e}")
    return removed

# 上传文件（仅保存）
@bp.route("/api/cloud/upload", methods=["POST"])
def upload_file():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "未接收到文件"}), 400

    original_filename = file.filename or ""
    if not original_filename:
        return jsonify({"error": "文件名为空"}), 400
    if not _is_allowed_extension(original_filename):
        return jsonify({"error": "不支持的文件类型"}), 400

    stored_name = _make_storage_name(original_filename)
    save_path = os.path.join(UPLOAD_FOLDER, stored_name)
    file.save(save_path)

    # 如果是图片文件，生成缩略图
    if is_image_file(stored_name):
        generate_thumbnail(save_path, stored_name)

    meta = _load_meta()
    meta[stored_name] = {
        "original_name": original_filename,
        "uploaded_at": time.time()
    }
    _save_meta(meta)

    message = f"✅ 文件已上传：{original_filename}"
    print(f"[UPLOAD] 文件已保存: {save_path}")

    return jsonify({
        "ok": True,
        "message": message,
        "filename": stored_name,
        "original_name": original_filename
    })


# 列出文件 + 存储空间信息
@bp.route("/api/cloud/files")
def list_files():
    sort_by = request.args.get("sort", "time")  # time 或 name
    order = request.args.get("order", "desc")   # asc 或 desc
    
    meta = _load_meta()
    files = []
    for f in os.listdir(UPLOAD_FOLDER):
        path = os.path.join(UPLOAD_FOLDER, f)
        if f == os.path.basename(UPLOAD_META_PATH):
            continue
        if os.path.isfile(path):
            size_mb = os.path.getsize(path) / (1024 * 1024)
            create_time = os.path.getctime(path)
            
            # 获取文件扩展名用于分类排序
            ext = os.path.splitext(f)[1].lower()
            
            # 检查是否有缩略图
            has_thumbnail = False
            if is_image_file(f):
                thumb_path = os.path.join(THUMBNAIL_FOLDER, f"thumb_{f}")
                has_thumbnail = os.path.exists(thumb_path)
            
            info = meta.get(f, {})
            display_name = info.get("original_name", f)
            files.append({
                "name": f,
                "stored_name": f,
                "original_name": info.get("original_name", f),
                "display_name": display_name,
                "size": f"{size_mb:.2f} MB",
                "create_time": create_time,
                "ext": ext,
                "has_thumbnail": has_thumbnail
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
@bp.route("/api/cloud/delete/<path:name>", methods=["POST"])
def delete_file(name):
    """删除文件 - 需要登录"""
    # 检查登录状态
    if not session.get("logged_in"):
        return jsonify({"error": "需要登录后才能删除文件", "require_login": True}), 403
    
    try:
        meta = _load_meta()
        stored_name = _resolve_stored_name(name, meta)
        if not stored_name:
            return jsonify({"message": "未找到文件"}), 404
        os.remove(os.path.join(UPLOAD_FOLDER, stored_name))
        if is_image_file(stored_name):
            thumb_path = os.path.join(THUMBNAIL_FOLDER, f"thumb_{stored_name}")
            if os.path.exists(thumb_path):
                os.remove(thumb_path)
        if stored_name in meta:
            meta.pop(stored_name)
            _save_meta(meta)
        removed = cleanup_orphan_thumbnails()
        msg = f"🗑️ 已删除 {name}"
        if removed:
            msg += f"，并清理多余缩略图 {len(removed)} 个"
        return jsonify({"message": msg, "removed_thumbnails": removed})
    except Exception as e:
        return jsonify({"message": f"删除失败: {e}"}), 500


# 文件下载
@bp.route("/api/cloud/download/<path:filename>")
def download_file(filename):
    meta = _load_meta()
    stored_name = _resolve_stored_name(filename, meta)
    if not stored_name:
        return jsonify({"error": "未找到文件"}), 404
    info = meta.get(stored_name, {})
    download_name = info.get("original_name") or stored_name
    return send_from_directory(UPLOAD_FOLDER, stored_name, as_attachment=True, download_name=download_name)


# 文件访问（直接访问）
@bp.route("/uploads/<path:filename>")
def serve_upload(filename):
    meta = _load_meta()
    stored_name = _resolve_stored_name(filename, meta)
    if not stored_name:
        return jsonify({"error": "未找到文件"}), 404
    return send_from_directory(UPLOAD_FOLDER, stored_name)

# 缩略图访问
@bp.route("/thumbnails/<path:filename>")
def serve_thumbnail(filename):
    meta = _load_meta()
    if filename.startswith("thumb_"):
        original = filename[len("thumb_"):]
        stored_name = _resolve_stored_name(original, meta)
        if not stored_name:
            return jsonify({"error": "未找到文件"}), 404
        thumb_name = f"thumb_{stored_name}"
    else:
        stored_name = _resolve_stored_name(filename, meta)
        if not stored_name:
            return jsonify({"error": "未找到文件"}), 404
        thumb_name = f"thumb_{stored_name}"
    return send_from_directory(THUMBNAIL_FOLDER, thumb_name)

# 手动清理缩略图
@bp.route("/api/cloud/clean_thumbnails", methods=["POST"])
def clean_thumbnails():
    """清理多余缩略图 - 需要登录"""
    if not session.get("logged_in"):
        return jsonify({"error": "需要登录后才能清理缩略图", "require_login": True}), 403

    removed = cleanup_orphan_thumbnails()
    return jsonify({
        "message": f"✅ 已清理多余缩略图 {len(removed)} 个",
        "removed_thumbnails": removed
    })
