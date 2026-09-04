import os
import shutil
import time

from flask import Blueprint, jsonify, render_template, request, send_from_directory, session, url_for

from modules.auth.user_store import user_has_permission
from modules.cloud.service import (
    clean_file_type,
    cleanup_orphan_thumbnails,
    is_image_file,
    save_uploaded_bytes,
    save_uploaded_file,
    save_uploaded_stream,
)
from modules.cloud.storage import (
    THUMBNAIL_FOLDER,
    UPLOAD_FOLDER,
    UPLOAD_META_PATH,
    load_meta,
    resolve_stored_name,
    save_meta,
)
from modules.cloud.sharing import ShareTokenError, create_share, list_shares, resolve_share, revoke_share
from modules.cloud.endpoints import load_public_origins, share_links


bp = Blueprint("cloud", __name__)
MIN_FREE_BYTES = 5 * 1024**3
LEGACY_UPLOAD_LIMIT = 500 * 1024**2


def _cloud_delete_denied():
    logged_in = bool(session.get("logged_in"))
    return jsonify({
        "success": False,
        "error": "需要专属用户或管理员权限才能管理云盘文件",
        "require_login": not logged_in,
        "required_permission": "cloud:delete",
    }), 403


def _can_delete_cloud():
    return session.get("logged_in") and user_has_permission(session.get("user"), "cloud:delete")


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
    is_stream_upload = request.headers.get("X-Cloud-Raw-Upload") == "1"
    if not is_stream_upload and request.content_length and request.content_length > LEGACY_UPLOAD_LIMIT:
        return jsonify({
            "success": False,
            "error": "传统上传接口最大支持 500 MB，请使用 Cloud 网页进行大型文件流式上传",
            "code": "LEGACY_UPLOAD_TOO_LARGE",
        }), 413
    if request.content_length:
        free = shutil.disk_usage(UPLOAD_FOLDER).free
        if request.content_length > max(0, free - MIN_FREE_BYTES):
            return jsonify({
                "success": False,
                "error": "空间不足：上传完成后必须至少保留 5 GB 可用空间",
                "code": "INSUFFICIENT_STORAGE",
            }), 507
    if is_stream_upload:
        meta = load_meta()
        saved, error = save_uploaded_stream(
            request.stream,
            _raw_upload_filename(),
            meta,
            expected_size=request.content_length,
            minimum_free_bytes=MIN_FREE_BYTES,
        )
        if error:
            return jsonify({
                "success": False,
                "error": error.get("error", "上传失败"),
                "code": "UPLOAD_FAILED",
            }), 507 if "空间不足" in error.get("error", "") else 400
        save_meta(meta)
        return jsonify({
            "success": True,
            "message": f"✅ 文件已上传：{saved['original_name']}",
            "data": saved,
        })

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
        "available_for_upload": f"{max(0, free - MIN_FREE_BYTES) / (1024**3):.2f} GB",
        "minimum_free": "5.00 GB",
    }

    return jsonify({
        "files": files,
        "disk": disk,
    })


@bp.route("/api/cloud/delete/<path:name>", methods=["POST"])
def delete_file(name):
    if not _can_delete_cloud():
        return _cloud_delete_denied()

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


@bp.route("/api/cloud/share/<path:filename>", methods=["POST"])
def create_file_share(filename):
    payload = request.get_json(silent=True) or {}
    payload["files"] = [filename]
    return _create_share_response(payload)


@bp.route("/api/cloud/share", methods=["POST"])
def create_batch_share():
    return _create_share_response(request.get_json(silent=True) or {})


def _create_share_response(payload):
    requested = payload.get("files") or []
    if not isinstance(requested, list) or not requested or len(requested) > 100:
        return jsonify({"success": False, "error": "请选择1至100个文件"}), 400
    meta = load_meta()
    files, seen = [], set()
    for filename in requested:
        stored_name = resolve_stored_name(str(filename), meta)
        if not stored_name or stored_name in seen:
            continue
        seen.add(stored_name)
        files.append({"stored_name": stored_name,
                      "display_name": meta.get(stored_name, {}).get("original_name") or stored_name})
    if not files:
        return jsonify({"success": False, "error": "未找到可分享的文件"}), 404
    token, record = create_share(files, payload.get("expires_in", 7 * 24 * 60 * 60))
    path = url_for("cloud.download_shared_file", token=token)
    return jsonify({
        "success": True,
        "message": "临时分享链接已生成",
        "data": {
            "path": path,
            "links": share_links(path),
            "expires_at": record["expires_at"],
            "file_count": len(files),
        },
    })


@bp.route("/s/cloud/<token>")
def download_shared_file(token):
    try:
        record = resolve_share(token)
    except ShareTokenError as error:
        return jsonify({"success": False, "error": str(error)}), 410
    files = record.get("files", [])
    return render_template("cloud_share.html", files=files, token=token, expires_at=record["expires_at"])


@bp.route("/s/cloud/<token>/<int:file_index>")
def download_shared_file_item(token, file_index):
    try:
        files = resolve_share(token).get("files", [])
    except ShareTokenError as error:
        return jsonify({"success": False, "error": str(error)}), 410
    if file_index < 0 or file_index >= len(files):
        return jsonify({"success": False, "error": "分享的文件不存在"}), 404
    return _send_shared_file(files[file_index])


def _send_shared_file(file_info):
    if not file_info:
        return jsonify({"success": False, "error": "分享的文件不存在"}), 404
    meta = load_meta()
    stored_name = resolve_stored_name(file_info.get("stored_name"), meta)
    if not stored_name:
        return jsonify({"success": False, "error": "分享的文件不存在"}), 404
    info = meta.get(stored_name, {})
    download_name = info.get("original_name") or stored_name
    return send_from_directory(
        UPLOAD_FOLDER,
        stored_name,
        as_attachment=True,
        download_name=download_name,
    )


@bp.route("/api/cloud/shares")
def manage_file_shares():
    if not _can_delete_cloud():
        return _cloud_delete_denied()
    shares = list_shares()
    origins = load_public_origins()
    for share in shares:
        token = share.pop("token", None)
        share["path"] = url_for("cloud.download_shared_file", token=token) if token else None
        share["links"] = share_links(share["path"], origins) if share["path"] else []
    return jsonify({"success": True, "data": {"shares": shares}})


@bp.route("/api/cloud/shares/<share_id>", methods=["DELETE"])
def delete_file_share(share_id):
    if not _can_delete_cloud():
        return _cloud_delete_denied()
    if not revoke_share(share_id):
        return jsonify({"success": False, "error": "未找到分享记录"}), 404
    return jsonify({"success": True, "message": "分享链接已撤销"})


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
    if not _can_delete_cloud():
        return _cloud_delete_denied()

    removed = cleanup_orphan_thumbnails()
    return jsonify({
        "success": True,
        "message": f"✅ 已清理多余缩略图 {len(removed)} 个",
        "removed_thumbnails": removed,
    })
