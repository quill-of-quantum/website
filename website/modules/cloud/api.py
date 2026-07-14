import os
import shutil
import time

from flask import Blueprint, jsonify, request, send_from_directory, session

from modules.cloud.service import (
    clean_file_type,
    cleanup_orphan_thumbnails,
    is_image_file,
    save_uploaded_bytes,
    save_uploaded_file,
)
from modules.cloud.storage import (
    THUMBNAIL_FOLDER,
    UPLOAD_FOLDER,
    UPLOAD_META_PATH,
    load_meta,
    resolve_stored_name,
    save_meta,
)


bp = Blueprint("cloud", __name__)


def _raw_upload_filename():
    original_filename = (
        request.args.get("filename")
        or request.args.get("name")
        or request.headers.get("filename")
        or request.headers.get("X-Filename")
        or ""
    ).strip()
    file_type = (
        request.args.get("type")
        or request.headers.get("type")
        or request.headers.get("X-File-Type")
        or request.headers.get("Content-Type")
        or ""
    )
    file_type = clean_file_type(file_type)

    if not original_filename:
        original_filename = f"shortcut_upload_{time.strftime('%Y%m%d_%H%M%S')}"
    if original_filename and not os.path.splitext(original_filename)[1] and file_type:
        original_filename = f"{original_filename}.{file_type}"
    return original_filename


@bp.route("/api/cloud/upload", methods=["POST"])
def upload_file():
    files = request.files.getlist("file")
    if not files:
        files = [file for _, file in request.files.items()]
    files = [file for file in files if file and file.filename]

    meta = load_meta()
    if not files:
        raw_data = request.get_data()
        original_filename = _raw_upload_filename()
        saved, error = save_uploaded_bytes(raw_data, original_filename, meta)
    else:
        saved, error = save_uploaded_file(files[0], meta)

    if error:
        return jsonify({
            "success": False,
            "error": error.get("error", "上传失败"),
            "code": "UPLOAD_FAILED",
        }), 400

    save_meta(meta)
    message = f"✅ 文件已上传：{saved['original_name']}"
    return jsonify({
        "success": True,
        "message": message,
        "data": saved,
    })


@bp.route("/api/cloud/files")
def list_files():
    sort_by = request.args.get("sort", "time")
    order = request.args.get("order", "desc")

    meta = load_meta()
    files = []
    for f in os.listdir(UPLOAD_FOLDER):
        path = os.path.join(UPLOAD_FOLDER, f)
        if f == os.path.basename(UPLOAD_META_PATH):
            continue
        if os.path.isfile(path):
            size_mb = os.path.getsize(path) / (1024 * 1024)
            create_time = os.path.getctime(path)
            ext = os.path.splitext(f)[1].lower()

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
                "has_thumbnail": has_thumbnail,
            })

    if sort_by == "time":
        files.sort(key=lambda x: x["create_time"], reverse=(order == "desc"))
    elif sort_by == "name":
        files.sort(key=lambda x: (x["ext"], x["name"].lower()), reverse=(order == "desc"))

    total, used, free = shutil.disk_usage(UPLOAD_FOLDER)
    disk = {
        "total": f"{total / (1024**3):.2f} GB",
        "used": f"{used / (1024**3):.2f} GB",
        "free": f"{free / (1024**3):.2f} GB",
    }

    return jsonify({
        "files": files,
        "disk": disk,
    })


@bp.route("/api/cloud/delete/<path:name>", methods=["POST"])
def delete_file(name):
    if not session.get("logged_in"):
        return jsonify({"success": False, "error": "需要登录后才能删除文件", "require_login": True}), 403

    try:
        meta = load_meta()
        stored_name = resolve_stored_name(name, meta)
        if not stored_name:
            return jsonify({"success": False, "message": "未找到文件"}), 404

        os.remove(os.path.join(UPLOAD_FOLDER, stored_name))
        if is_image_file(stored_name):
            thumb_path = os.path.join(THUMBNAIL_FOLDER, f"thumb_{stored_name}")
            if os.path.exists(thumb_path):
                os.remove(thumb_path)

        if stored_name in meta:
            meta.pop(stored_name)
            save_meta(meta)

        removed = cleanup_orphan_thumbnails()
        msg = f"🗑️ 已删除 {name}"
        if removed:
            msg += f"，并清理多余缩略图 {len(removed)} 个"
        return jsonify({"success": True, "message": msg, "removed_thumbnails": removed})
    except Exception as e:
        return jsonify({"success": False, "message": f"删除失败: {e}"}), 500


@bp.route("/api/cloud/download/<path:filename>")
def download_file(filename):
    meta = load_meta()
    stored_name = resolve_stored_name(filename, meta)
    if not stored_name:
        return jsonify({"success": False, "error": "未找到文件"}), 404
    info = meta.get(stored_name, {})
    download_name = info.get("original_name") or stored_name
    return send_from_directory(UPLOAD_FOLDER, stored_name, as_attachment=True, download_name=download_name)


@bp.route("/uploads/<path:filename>")
def serve_upload(filename):
    meta = load_meta()
    stored_name = resolve_stored_name(filename, meta)
    if not stored_name:
        return jsonify({"success": False, "error": "未找到文件"}), 404
    return send_from_directory(UPLOAD_FOLDER, stored_name)


@bp.route("/thumbnails/<path:filename>")
def serve_thumbnail(filename):
    meta = load_meta()
    if filename.startswith("thumb_"):
        original = filename[len("thumb_"):]
        stored_name = resolve_stored_name(original, meta)
        if not stored_name:
            return jsonify({"success": False, "error": "未找到文件"}), 404
        thumb_name = f"thumb_{stored_name}"
    else:
        stored_name = resolve_stored_name(filename, meta)
        if not stored_name:
            return jsonify({"success": False, "error": "未找到文件"}), 404
        thumb_name = f"thumb_{stored_name}"
    return send_from_directory(THUMBNAIL_FOLDER, thumb_name)


@bp.route("/api/cloud/clean_thumbnails", methods=["POST"])
def clean_thumbnails():
    if not session.get("logged_in"):
        return jsonify({"success": False, "error": "需要登录后才能清理缩略图", "require_login": True}), 403

    removed = cleanup_orphan_thumbnails()
    return jsonify({
        "success": True,
        "message": f"✅ 已清理多余缩略图 {len(removed)} 个",
        "removed_thumbnails": removed,
    })
