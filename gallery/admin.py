import io
import json
import os

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for
from PIL import Image, ExifTags

try:
    import piexif
except Exception:  # pragma: no cover - optional dependency
    piexif = None

admin_bp = Blueprint("admin", __name__)


def allowed_file(filename):
    extensions = current_app.config.get("GALLERY_ALLOWED_EXTENSIONS", {"jpg", "jpeg"})
    return "." in filename and filename.rsplit(".", 1)[1].lower() in extensions


def _decode_user_comment(raw):
    if raw is None:
        return None
    if isinstance(raw, bytes):
        for prefix in (b"ASCII\0\0\0", b"UNICODE\0\0"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break
        for encoding in ("utf-8", "utf-16-le", "utf-16"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("latin-1", errors="ignore")
    return str(raw)


def extract_species(image_data):
    user_comment = None
    if isinstance(image_data, (bytes, bytearray)):
        data = bytes(image_data)
        if piexif:
            try:
                exif_dict = piexif.load(data)
                user_comment = exif_dict.get("Exif", {}).get(piexif.ExifIFD.UserComment)
            except Exception:
                user_comment = None
        if user_comment is None:
            image = Image.open(io.BytesIO(data))
            exif = image.getexif()
            user_comment_tag = None
            for tag, name in ExifTags.TAGS.items():
                if name == "UserComment":
                    user_comment_tag = tag
                    break
            user_comment = exif.get(user_comment_tag)
    else:
        if piexif:
            try:
                data = image_data.read()
                image_data.seek(0)
                exif_dict = piexif.load(data)
                user_comment = exif_dict.get("Exif", {}).get(piexif.ExifIFD.UserComment)
            except Exception:
                user_comment = None
        if user_comment is None:
            image = Image.open(image_data)
            exif = image.getexif()
            user_comment_tag = None
            for tag, name in ExifTags.TAGS.items():
                if name == "UserComment":
                    user_comment_tag = tag
                    break
            user_comment = exif.get(user_comment_tag)
    user_comment = _decode_user_comment(user_comment)
    if not user_comment:
        return None, "缺少 EXIF UserComment"
    try:
        payload = json.loads(user_comment)
    except json.JSONDecodeError:
        return None, "EXIF UserComment 不是有效 JSON"
    species = (payload.get("species") or "").strip()
    if not species:
        return None, "EXIF UserComment 缺少 species"
    return species, None


def sanitize_species(name):
    cleaned = name.strip().replace(os.sep, "_")
    if os.altsep:
        cleaned = cleaned.replace(os.altsep, "_")
    cleaned = cleaned.replace("\x00", "")
    return cleaned.strip()


def save_photo(file_storage):
    if not allowed_file(file_storage.filename):
        return None, f"{file_storage.filename} 不是 JPG/JPEG 文件"
    try:
        file_storage.stream.seek(0)
    except Exception:
        pass
    data = file_storage.read()
    incoming_size = len(data)
    if incoming_size == 0:
        return None, f"{file_storage.filename} 读取失败"
    species, err = extract_species(data)
    if err:
        return None, f"{file_storage.filename} 解析失败：{err}"
    safe_species = sanitize_species(species)
    if not safe_species:
        return None, f"{file_storage.filename} species 无效"
    photos_root = current_app.config.get(
        "GALLERY_PHOTOS_ROOT", os.path.join(os.path.dirname(__file__), "photos")
    )
    species_dir = os.path.join(photos_root, safe_species)
    os.makedirs(species_dir, exist_ok=True)
    original_name = os.path.basename(file_storage.filename)
    target_path = os.path.join(species_dir, original_name)
    if os.path.exists(target_path):
        try:
            existing_size = os.path.getsize(target_path)
        except OSError:
            existing_size = None
        if existing_size == incoming_size:
            return {
                "filename": file_storage.filename,
                "species": species,
                "saved_to": os.path.relpath(target_path, photos_root),
                "skipped": True,
            }, None
    with open(target_path, "wb") as f:
        f.write(data)
    return {
        "filename": file_storage.filename,
        "species": species,
        "saved_to": os.path.relpath(target_path, photos_root),
    }, None


@admin_bp.route("/1/", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if (
            username == current_app.config.get("ADMIN_USERNAME", "admin")
            and password == current_app.config.get("ADMIN_PASSWORD", "bbdwz")
        ):
            session["logged_in"] = True
            return redirect(url_for("admin.admin_dashboard"))
        error = "账号或密码不正确"
    return render_template("login.html", error=error)


@admin_bp.route("/admin/", methods=["GET", "POST"])
def admin_dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("admin.login"))
    results = []
    errors = []
    if request.method == "POST":
        files = request.files.getlist("photos")
        if not files:
            errors.append("请选择要上传的图片")
        for file in files:
            if not file or file.filename == "":
                continue
            try:
                result, err = save_photo(file)
                if err:
                    errors.append(err)
                else:
                    results.append(result)
            except Exception as exc:
                errors.append(f"{file.filename} 上传失败：{exc}")
    return render_template("admin.html", results=results, errors=errors)


@admin_bp.route("/admin/upload", methods=["POST"])
def admin_upload():
    if not session.get("logged_in"):
        return {"ok": False, "errors": ["未登录"]}, 401
    files = request.files.getlist("photos")
    if not files:
        return {"ok": False, "errors": ["请选择要上传的图片"]}, 400
    results = []
    errors = []
    for file in files:
        if not file or file.filename == "":
            continue
        try:
            result, err = save_photo(file)
            if err:
                errors.append(err)
            else:
                results.append(result)
        except Exception as exc:
            errors.append(f"{file.filename} 上传失败：{exc}")
    return {"ok": len(errors) == 0, "results": results, "errors": errors}


@admin_bp.route("/admin/upload-one", methods=["POST"])
def admin_upload_one():
    if not session.get("logged_in"):
        return {"ok": False, "error": "未登录"}, 401
    file = request.files.get("photo")
    if not file or file.filename == "":
        return {"ok": False, "error": "请选择要上传的图片"}, 400
    try:
        result, err = save_photo(file)
        if err:
            return {"ok": False, "error": err}, 400
        return {"ok": True, "result": result}
    except Exception as exc:
        return {"ok": False, "error": f"{file.filename} 上传失败：{exc}"}, 500


@admin_bp.route("/admin/preflight", methods=["POST"])
def admin_preflight():
    if not session.get("logged_in"):
        return {"ok": False, "error": "未登录"}, 401
    payload = request.get_json(silent=True) or {}
    files = payload.get("files") or []
    if not isinstance(files, list):
        return {"ok": False, "error": "参数错误"}, 400
    photos_root = current_app.config.get(
        "GALLERY_PHOTOS_ROOT", os.path.join(os.path.dirname(__file__), "photos")
    )
    existing = {}
    if os.path.isdir(photos_root):
        for dirpath, _, filenames in os.walk(photos_root):
            for name in filenames:
                try:
                    size = os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    continue
                existing.setdefault(name, set()).add(size)
    to_upload = []
    skipped = []
    for item in files:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("name") or "")
        size = item.get("size")
        if not filename or size is None:
            continue
        sizes = existing.get(os.path.basename(filename), set())
        if int(size) in sizes:
            skipped.append({"name": filename, "size": size})
        else:
            to_upload.append({"name": filename, "size": size})
    return {"ok": True, "to_upload": to_upload, "skipped": skipped}


@admin_bp.route("/logout/")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("admin.login"))
