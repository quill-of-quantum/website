import os

from flask import Flask, render_template, send_from_directory, url_for

from admin import admin_bp

app = Flask(__name__)
app.secret_key = os.environ.get("GALLERY_SECRET_KEY", "dev-secret-change-me")

app.config["ADMIN_USERNAME"] = os.environ.get("GALLERY_ADMIN_USER", "admin")  # 登录名
app.config["ADMIN_PASSWORD"] = os.environ.get("GALLERY_ADMIN_PASS", "bbdwz")  # 登录密码
app.config["GALLERY_ALLOWED_EXTENSIONS"] = {"jpg", "jpeg"}
app.config["GALLERY_PHOTOS_ROOT"] = os.path.join(os.path.dirname(__file__), "photos")
app.register_blueprint(admin_bp)


@app.route("/")
def index():
    photos_root = app.config["GALLERY_PHOTOS_ROOT"]
    photos = []
    species_set = set()
    if os.path.isdir(photos_root):
        for dirpath, _, filenames in os.walk(photos_root):
            rel_dir = os.path.relpath(dirpath, photos_root)
            species = rel_dir if rel_dir != "." else "未分类"
            for filename in sorted(filenames):
                if not filename.lower().endswith((".jpg", ".jpeg")):
                    continue
                rel_path = os.path.join(rel_dir, filename) if rel_dir != "." else filename
                photos.append(
                    {
                        "url": url_for("photo_file", filename=rel_path.replace(os.sep, "/")),
                        "species": species,
                        "name": os.path.splitext(filename)[0],
                    }
                )
                species_set.add(species)
    photos.sort(key=lambda item: (item["species"], item["name"]))
    return render_template(
        "index.html",
        photos=photos,
        photo_count=len(photos),
        species_count=len(species_set),
    )


@app.route("/photos/<path:filename>")
def photo_file(filename):
    photos_root = app.config["GALLERY_PHOTOS_ROOT"]
    return send_from_directory(photos_root, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
