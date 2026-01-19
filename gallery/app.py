import os

from flask import Flask, render_template, send_file, send_from_directory, url_for
from PIL import Image, ImageOps

from admin import admin_bp
from featured import get_featured_map, get_group_order
from gallery_data import build_groups

app = Flask(__name__)
app.secret_key = os.environ.get("GALLERY_SECRET_KEY", "dev-secret-change-me")

app.config["ADMIN_USERNAME"] = os.environ.get("GALLERY_ADMIN_USER", "admin")  # 登录名
app.config["ADMIN_PASSWORD"] = os.environ.get("GALLERY_ADMIN_PASS", "bbdwz")  # 登录密码
app.config["GALLERY_ALLOWED_EXTENSIONS"] = {"jpg", "jpeg"}
app.config["GALLERY_PHOTOS_ROOT"] = os.path.join(os.path.dirname(__file__), "photos")
app.config["GALLERY_DB_PATH"] = os.path.join(os.path.dirname(__file__), "featured.db")
app.register_blueprint(admin_bp)


@app.route("/")
def index():
    photos_root = app.config["GALLERY_PHOTOS_ROOT"]
    featured_map = get_featured_map(app.config["GALLERY_DB_PATH"])
    group_order = get_group_order(app.config["GALLERY_DB_PATH"])
    group_list = build_groups(
        photos_root,
        lambda path: url_for("photo_file", filename=path),
        lambda path: url_for("photo_thumb", size=360, filename=path),
        featured_map,
        group_order,
    )
    return render_template(
        "index.html",
        groups=group_list,
        photo_count=sum(group["count"] for group in group_list),
        species_count=len(group_list),
    )


@app.route("/photos/<path:filename>")
def photo_file(filename):
    photos_root = app.config["GALLERY_PHOTOS_ROOT"]
    return send_from_directory(photos_root, filename)


@app.route("/thumbs/<int:size>/<path:filename>")
def photo_thumb(size, filename):
    photos_root = app.config["GALLERY_PHOTOS_ROOT"]
    safe_name = os.path.normpath(filename).lstrip(os.sep)
    if safe_name.startswith(".."):
        return {"error": "invalid path"}, 400
    if ".thumbs" in safe_name.split(os.sep):
        return {"error": "invalid path"}, 400
    source_path = os.path.join(photos_root, safe_name)
    if not os.path.isfile(source_path):
        return {"error": "not found"}, 404
    size = max(160, min(size, 1200))
    thumb_root = os.path.join(photos_root, ".thumbs", str(size))
    thumb_path = os.path.join(thumb_root, safe_name)
    os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
    try:
        source_mtime = os.path.getmtime(source_path)
        thumb_mtime = os.path.getmtime(thumb_path) if os.path.exists(thumb_path) else 0
        if source_mtime > thumb_mtime:
            with Image.open(source_path) as img:
                img = ImageOps.exif_transpose(img)
                img.thumbnail((size, size * 10))
                img.save(thumb_path, "JPEG", quality=70, optimize=True, progressive=True)
    except Exception:
        return send_from_directory(photos_root, safe_name)
    return send_file(thumb_path, mimetype="image/jpeg", conditional=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
