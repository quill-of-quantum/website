非常棒 👍 你已经到了“发布版本”的阶段。
我可以帮你把原本的 `README.md` 全面更新成**现在项目的真实状态**（包括物流追踪系统、自动调度服务、日志持久化、自启优化等）。

下面是 **新版 README.md 完整内容** —— 你可以直接覆盖原文件使用。

---

# 🧭 Raspberry Pi Website + Tracker System

这是一个运行在树莓派上的综合型网站项目，基于 **Python (Flask)** 与 **Conda 环境** 构建，具备网站展示、后台管理、自动物流追踪与 systemd 自启功能。

---

## 🌐 功能总览

### 🔓 公共访问区

* 首页：展示系统 CPU、内存占用
* 工具区：预留扩展功能
* 物流追踪面板 `/tracker`

  * 可添加、删除追踪任务
  * 查看实时运行状态与详细路线
  * 支持手动刷新与自动后台更新
* 天气 API `/api/weather`（开放接口）

### 🔒 管理员区

* 隐藏入口 `/admin-portal-7f3b`
* 登录认证 + 管理面板
* 支持命令日志写入 `/home/bbdwz/admin_commands.log`

### ⚙️ 后台守护与自动运行

* 网站服务（`website.service`）由 systemd 管理
* 自动物流调度器（`tracker_scheduler.service`）每分钟检查一次任务状态
* 崩溃自动重启
* 日志写入 `/var/log/tracker.log`

---

## 🧱 项目结构

```
/home/bbdwz/projects/website/
├── app.py                     # Flask 主程序入口
├── database.db                # 历史遗留数据库（当前以 tracker.db 为主）
├── database.db.old            # 数据库旧版本备份
├── init_db.py                 # 初始化数据库脚本
├── README.md                  # 本说明文档
├── shortcut.log               # 快捷命令记录
├── static/
│   └── js/
│       ├── OrbitControls.js
│       ├── pako.min.js
│       ├── three.min.js
│       └── VolumeShader.js
├── templates/
│   ├── 3d_test.html
│   ├── admin_index.html
│   ├── index.html
│   ├── login.html
│   ├── tools.html
│   ├── tools.html.backup
│   ├── tracker.html
│   └── viewer.html
├── tools_api.py               # 工具接口
├── tracker_api.py             # 物流追踪接口
├── tracker_browser.py         # 自动抓取与比对逻辑 (Playwright)
├── tracker.db                 # SQLite 主数据库
├── tracker_last.json          # 最近一次抓取详情缓存
├── tracker_result.html        # 抓取结果 HTML 缓存
├── tracker_scheduler.py       # 后台定时调度器
├── uploads/                   # 上传文件目录
│   ├── {9C8949C9-50F6-45E7-AB10-E648D7875B76}.png
│   ├── DSC01058-DxO_DeepPRIME XD2s.jpg
│   ├── {F8299AD5-3F24-4F53-9996-5EBB5A224866}.png
│   ├── IMG_5572.jpeg
│   ├── IMG_5573.jpeg
│   ├── IMG_5574.jpeg
│   └── IMG_5575.jpeg
├── utils/                     # 实用脚本（预留）
├── weather/                   # 天气与能耗分析模块
│   ├── analyze_weather.py
│   ├── daily_usage.csv
│   ├── forecast_usage.csv
│   ├── hourly_usage.csv
│   ├── number.txt
│   ├── temperature_inside.csv
│   ├── test.py                # 天气模块单元测试
│   ├── usage_cumulative.svg
│   ├── usage_daily.svg
│   ├── usage_forecast.svg
│   ├── usage_hourly.svg
│   └── usage_pattern.svg
├── website_env_backup.yml     # Conda 环境备份
├── __pycache__/               # 运行后生成的缓存
│   ├── app.cpython-311.pyc
│   ├── tools_api.cpython-311.pyc
│   ├── tracker_api.cpython-311.pyc
│   └── tracker_browser.cpython-311.pyc
└── weather/__pycache__/…      # 天气模块缓存
```

> 由于 `__pycache__` 目录为 Python 运行后自动生成，必要时可忽略或加入 `.gitignore`，此处仅保留以反映当前树莓派环境的真实状态。

---

## 🧩 系统环境

* OS：Raspberry Pi OS Lite (64-bit)
* Python：来自 Conda 环境
* 依赖：

  ```bash
  flask psutil requests gunicorn playwright beautifulsoup4
  ```

安装环境示例：

```bash
conda create -n web python=3.11 -y
conda activate web
pip install flask psutil requests gunicorn playwright beautifulsoup4
playwright install chromium
```

---

## 🚀 运行方式

### 1️⃣ 手动运行（调试模式）

```bash
conda activate web
cd ~/projects/website
python app.py
```

访问：

```
http://<树莓派IP>:5000/
```

### 2️⃣ 自动运行（生产模式）

#### 🔹 Website 服务

文件：`/etc/systemd/system/website.service`

```ini
[Unit]
Description=Gunicorn service for Raspberry Pi Flask website
After=network.target

[Service]
User=bbdwz
Group=www-data
WorkingDirectory=/home/bbdwz/projects/website
Environment="PATH=/home/bbdwz/miniconda3/envs/web/bin"
ExecStart=/home/bbdwz/miniconda3/envs/web/bin/gunicorn -w 3 -b unix:/home/bbdwz/website.sock app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### 🔹 Tracker 调度服务

文件：`/etc/systemd/system/tracker_scheduler.service`

```ini
[Unit]
Description=Raspberry Pi Tracker Scheduler
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=bbdwz
Group=bbdwz
WorkingDirectory=/home/bbdwz/projects/website
Environment="PATH=/home/bbdwz/miniconda3/envs/web/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/home/bbdwz/miniconda3/envs/web/bin/python /home/bbdwz/projects/website/tracker_scheduler.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/tracker.log
StandardError=append:/var/log/tracker.log

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable website tracker_scheduler
sudo systemctl start website tracker_scheduler
```

---

## 📦 数据与日志

* 数据库文件：`/home/bbdwz/projects/website/tracker.db`
* 最近物流缓存：`tracker_last.json`
* 调度器日志：`/var/log/tracker.log`
* 网站访问日志：`/var/log/nginx/website_access.log`
* 网站错误日志：`/var/log/nginx/website_error.log`

---

## 🧭 网站结构

| 区域     | 路径                               | 权限                     |
| ------ | -------------------------------- | ---------------------- |
| 首页     | `/`                              | 所有人可访问（含系统信息 + 最新物流摘要） |
| 物流任务管理 | `/tracker`                       | 所有人可访问（动态更新）           |
| 天气接口   | `/api/weather`                   | 所有人可访问                 |
| 管理员入口  | `/admin-portal-7f3b/login`       | 登录后访问后台                |
| 命令接口   | `/admin-portal-7f3b/api/command` | 管理员权限                  |

---

## 🔁 自动化调度逻辑

* 每分钟运行一次任务检查
* 若距上次执行时间 ≥ `interval_minutes`，则自动执行抓取
* 抓取使用 Playwright 自动访问 tracking.nextsls.com
* 结果写入：

  * `tracker_last.json` 保存最新详情
  * `tracker.db` 更新 `last_run` 与 `last_status`
* 无更新时不覆盖旧物流数据，保持上次有效信息

---

## 🔒 开机自启验证

```bash
sudo systemctl is-enabled website
sudo systemctl is-enabled tracker_scheduler
```

输出：

```
enabled
enabled
```

---

## 🔍 调试与日志查看

```bash
# 查看网站
sudo systemctl status website
sudo journalctl -u website -n 30

# 查看调度器
sudo systemctl status tracker_scheduler
sudo tail -f /var/log/tracker.log
```

---

## 🪣 备份脚本（推荐）

文件：`/home/bbdwz/projects/website/backup_website.sh`

```bash
#!/bin/bash
BACKUP_DIR="/home/bbdwz/backups/website_$(date +%F_%H-%M-%S)"
mkdir -p $BACKUP_DIR
cp -r /home/bbdwz/projects/website $BACKUP_DIR/
cp /etc/systemd/system/website.service $BACKUP_DIR/
cp /etc/systemd/system/tracker_scheduler.service $BACKUP_DIR/
cp /etc/nginx/sites-available/website $BACKUP_DIR/
echo "✅ Backup completed: $BACKUP_DIR"
```

运行：

```bash
bash backup_website.sh
```

---

## ✅ 当前功能完成度

| 模块                        | 状态     |
| ------------------------- | ------ |
| Conda 环境                  | ✅      |
| Flask 主网站                 | ✅      |
| 管理员系统                     | ✅      |
| Tracker API + 界面          | ✅      |
| 自动抓取与对比                   | ✅      |
| 调度器 systemd 守护            | ✅      |
| 日志输出与持久化                  | ✅      |
| Nginx 反向代理                | ✅      |
| 开机自启                      | ✅      |
| 自动备份                      | 🔜（可选） |
| 公网部署 (HTTPS + Cloudflare) | 🔜 待做  |

---

## 🧠 提示与建议

* 建议使用 `sudo reboot` 测试完整自启流程

* 重启后执行：

  ```bash
  sudo systemctl status tracker_scheduler
  sudo tail -n 10 /var/log/tracker.log
  ```

  验证调度器是否正常运行

* 若要开放公网访问，请配置：

  * Nginx + HTTPS (Let’s Encrypt)
  * UFW 防火墙规则
  * fail2ban 保护 SSH

---

用户: **bbdwz**
主机: **bee**
环境: **Conda (web)**
系统: **Raspberry Pi OS Lite 64-bit**

---

是否希望我顺便为你生成一份对应的英文版 README（适合放 GitHub）？
我可以保留所有命令部分，只做自然翻译。
