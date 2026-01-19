import json
import os

from flask import Flask, redirect, render_template, request, session, url_for
from PIL import Image, ExifTags

try:
    import piexif
except Exception:  # pragma: no cover - optional dependency
    piexif = None

app = Flask(__name__)
app.secret_key = os.environ.get("GALLERY_SECRET_KEY", "dev-secret-change-me")

ADMIN_USERNAME = os.environ.get("GALLERY_ADMIN_USER", "admin") #登录名
ADMIN_PASSWORD = os.environ.get("GALLERY_ADMIN_PASS", "bbdwz") #登录密码
ALLOWED_EXTENSIONS = {"jpg", "jpeg"}
PHOTOS_ROOT = os.path.join(os.path.dirname(__file__), "photos")


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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


def extract_species(image_stream):
    user_comment = None
    if piexif:
        try:
            data = image_stream.read()
            image_stream.seek(0)
            exif_dict = piexif.load(data)
            user_comment = exif_dict.get("Exif", {}).get(piexif.ExifIFD.UserComment)
        except Exception:
            user_comment = None
    if user_comment is None:
        image = Image.open(image_stream)
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


def save_photo(file_storage):
    if not allowed_file(file_storage.filename):
        return None, f"{file_storage.filename} 不是 JPG/JPEG 文件"
    species, err = extract_species(file_storage.stream)
    file_storage.stream.seek(0)
    if err:
        return None, f"{file_storage.filename} 解析失败：{err}"
    safe_species = sanitize_species(species)
    if not safe_species:
        return None, f"{file_storage.filename} species 无效"
    species_dir = os.path.join(PHOTOS_ROOT, safe_species)
    os.makedirs(species_dir, exist_ok=True)
    original_name = os.path.basename(file_storage.filename)
    target_path = os.path.join(species_dir, original_name)
    file_storage.save(target_path)
    return {
        "filename": file_storage.filename,
        "species": species,
        "saved_to": os.path.relpath(target_path, PHOTOS_ROOT),
    }, None


def sanitize_species(name):
    cleaned = name.strip().replace(os.sep, "_")
    if os.altsep:
        cleaned = cleaned.replace(os.altsep, "_")
    cleaned = cleaned.replace("\x00", "")
    return cleaned.strip()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/1/", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("admin"))
        error = "账号或密码不正确"
    return render_template("login.html", error=error)


@app.route("/admin/", methods=["GET", "POST"])
def admin():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
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


@app.route("/admin/upload", methods=["POST"])
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


@app.route("/admin/upload-one", methods=["POST"])
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


@app.route("/logout/")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
