import os

from flask import Flask, render_template, send_from_directory, url_for

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
