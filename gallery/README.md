# Gallery Flask 站点（最小框架）

该项目是最小化的 Flask 站点框架，用于独立的个人画廊站点。当前只保留基础路由与模板目录，便于后续扩展。

---

## 功能概览

- 最小主页 `/`（Flask）
- 后台登录 `/1/` → `/admin/`
- 后台批量上传 JPG/JPEG（解析 EXIF UserComment JSON，按 species 保存）
- 静态资源目录 `/static/`
- 模板目录 `/templates/`
- 依赖：Pillow（读取 EXIF）

---

## 服务与端口

- **gallery.service**
  - Gunicorn + Flask（Unix Socket：`/home/bbdwz/gallery.sock`）

Nginx 反代：
- `listen 81` → `unix:/home/bbdwz/gallery.sock`（当前不依赖 `server_name` 分流）

cpolar 隧道：
- `a.eu.cpolar.io` → `localhost:81`
- `ggg.eu.cpolar.io` → `localhost:80`

---

## 页面与路由（UI）

- `/`：主页（`templates/index.html`）
- `/1/`：后台登录（`templates/login.html`）
- `/admin/`：后台上传（`templates/admin.html`）

---

## 目录结构

```
/home/bbdwz/projects/gallery/
├── app.py
├── templates/
│   └── index.html
└── static/
    └── style.css
```

---

## 关键配置

- Flask 入口：`app.py`（`app:app`）
- 环境变量：
  - `GALLERY_ADMIN_USER` / `GALLERY_ADMIN_PASS`：后台账号密码
  - `GALLERY_SECRET_KEY`：Session 密钥
- Unix Socket：`/home/bbdwz/gallery.sock`
- Nginx 站点配置：`/etc/nginx/sites-available/website`
- cpolar 配置：`/usr/local/etc/cpolar/cpolar.yml`

---

## 运维命令

```bash
# 服务状态
systemctl status gallery.service

# 重启服务
sudo systemctl restart gallery.service

# Nginx 校验 & 重载
sudo nginx -t
sudo systemctl reload nginx
```

---

## 日志位置

- Nginx 站点：`/var/log/nginx/gallery_error.log`, `/var/log/nginx/gallery_access.log`
- systemd 日志：
  - `journalctl -u gallery.service -n 200 --no-pager`
