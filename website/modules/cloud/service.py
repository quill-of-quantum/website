import os
import time
import uuid

from PIL import Image

from modules.cloud.storage import THUMBNAIL_FOLDER, UPLOAD_FOLDER


def is_allowed_extension(filename):
    return True


def make_storage_name(original_filename):
    ext = os.path.splitext(original_filename)[1].lower()
    return f"{uuid.uuid4().hex}{ext}"


def is_image_file(filename):
    return filename.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"))


def generate_thumbnail(file_path, filename):
    try:
        if not is_image_file(filename):
            return False

        with Image.open(file_path) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            img.thumbnail((120, 120), Image.Resampling.LANCZOS)

            thumb_name = f"thumb_{filename}"
            thumb_path = os.path.join(THUMBNAIL_FOLDER, thumb_name)
            img.save(thumb_path, "JPEG", quality=80, optimize=True)
            return True
    except Exception as e:
        print(f"生成缩略图失败: {e}")
        return False


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


def save_uploaded_file(file, meta):
    original_filename = file.filename or ""
    if not original_filename:
        return None, {"error": "文件名为空"}
    if not is_allowed_extension(original_filename):
        return None, {"error": f"不支持的文件类型: {original_filename}"}

    stored_name = make_storage_name(original_filename)
    save_path = os.path.join(UPLOAD_FOLDER, stored_name)
    file.save(save_path)

    if is_image_file(stored_name):
        generate_thumbnail(save_path, stored_name)

    meta[stored_name] = {
        "original_name": original_filename,
        "uploaded_at": time.time(),
    }

    print(f"[UPLOAD] 文件已保存: {save_path}")
    return {
        "filename": stored_name,
        "stored_name": stored_name,
        "original_name": original_filename,
        "size": os.path.getsize(save_path),
    }, None


def save_uploaded_bytes(data, original_filename, meta):
    original_filename = (original_filename or "").strip()
    if not original_filename:
        return None, {"error": "文件名为空，请在 URL 里加 ?filename=文件名"}
    if not data:
        return None, {"error": "请求体为空"}
    if not is_allowed_extension(original_filename):
        return None, {"error": f"不支持的文件类型: {original_filename}"}

    stored_name = make_storage_name(original_filename)
    save_path = os.path.join(UPLOAD_FOLDER, stored_name)
    with open(save_path, "wb") as f:
        f.write(data)

    if is_image_file(stored_name):
        generate_thumbnail(save_path, stored_name)

    meta[stored_name] = {
        "original_name": original_filename,
        "uploaded_at": time.time(),
    }

    print(f"[UPLOAD] 原始请求体文件已保存: {save_path}")
    return {
        "filename": stored_name,
        "stored_name": stored_name,
        "original_name": original_filename,
        "size": os.path.getsize(save_path),
    }, None


def save_uploaded_stream(stream, original_filename, meta, expected_size=None, minimum_free_bytes=0):
    original_filename = (original_filename or "").strip()
    if not original_filename:
        return None, {"error": "文件名为空"}

    stored_name = make_storage_name(original_filename)
    temp_folder = os.path.join(UPLOAD_FOLDER, ".direct_uploads")
    os.makedirs(temp_folder, exist_ok=True)
    temp_path = os.path.join(temp_folder, f"{stored_name}.part")
    save_path = os.path.join(UPLOAD_FOLDER, stored_name)
    written = 0

    try:
        with open(temp_path, "wb") as target:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                if minimum_free_bytes:
                    filesystem = os.statvfs(UPLOAD_FOLDER)
                    free = filesystem.f_bavail * filesystem.f_frsize
                    if free < minimum_free_bytes + len(chunk):
                        raise OSError("空间不足：必须至少保留 5 GB 可用空间")
                target.write(chunk)
                written += len(chunk)
            if expected_size is not None and written != int(expected_size):
                raise OSError(f"上传中断：仅收到 {written} / {expected_size} 字节")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp_path, save_path)
    except Exception as error:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
        return None, {"error": str(error) or "上传中断"}

    if is_image_file(stored_name):
        generate_thumbnail(save_path, stored_name)
    meta[stored_name] = {
        "original_name": original_filename,
        "uploaded_at": time.time(),
    }
    return {
        "filename": stored_name,
        "stored_name": stored_name,
        "original_name": original_filename,
        "size": written,
    }, None


def clean_file_type(file_type):
    file_type = (file_type or "").strip().lower().lstrip(".")
    if "/" in file_type:
        file_type = file_type.split("/", 1)[1]
    if "." in file_type:
        file_type = file_type.rsplit(".", 1)[1]
    file_type = "".join(ch for ch in file_type if ch.isalnum())
    return file_type or "bin"
