import os

from flask import Flask, render_template

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
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
