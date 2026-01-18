# Raspberry Pi Flask 网站 + 实时对战平台

该项目运行在树莓派，主站为 Flask（Gunicorn + Nginx），集成 Socket.IO 实时房间系统和 boardgame.io 棋类对战服务，并包含工具区、物流追踪、路线规划、字母棋识别等模块。

---

## 功能概览

- 首页 / 工具区 / 追踪面板 / 路线规划 / 3D 预览（Flask）
- 实时对战大厅 `/game/`（Socket.IO 房间管理 + boardgame.io 国际象棋嵌入）
- 字母棋识别与推荐 `/letter`（OCR + GADDAG）
- 后台追踪调度器（tracker_scheduler）
- 系统监控与天气图表输出

---

## 服务与端口

- **website.service**
  - Gunicorn + Flask 主站（Unix Socket：`/home/bbdwz/website.sock`）
- **boardgame.service**
  - boardgame.io Server（内部端口 `8000`）
- **tracker_scheduler.service**
  - 物流追踪后台调度

Nginx 反代：
- `/` → Flask（`/home/bbdwz/website.sock`）
- `/bgio/` → boardgame.io（`http://127.0.0.1:8000`）

---

## 页面与路由（UI）

- `/`：主页（`templates/index.html`）
- `/tools`：工具页（上传/文件浏览/缩略图）（`templates/tools.html`）
- `/tracker`：物流追踪面板（`templates/tracker.html`）
- `/map`：路线规划页面（`templates/map.html`）
- `/viewer`：3D 模型预览（`templates/viewer.html`）
- `/game/` 与 `/game/<room_id>`：实时对战大厅与房间（`templates/game.html`）
- `/letter`：字母棋识别与推荐（`templates/letter.html`）
- `/1/`：管理员面板（`templates/admin_index.html`）

备注：`templates/login.html` 存在，但登录通过 `/api/auth/login` 进行。

---

## 模块与目录（详细）

```
/home/bbdwz/projects/website/
├── app.py                        # Flask 主入口（注册蓝图、路由、管理区）
├── game/
│   ├── game_api.py               # Socket.IO 房间/座位/悔棋逻辑
│   └── boardgame/
│       ├── app/                  # boardgame.io 源码 + 构建
│       │   ├── server.js         # boardgame.io 服务端
│       │   ├── src/              # React 前端源码
│       │   └── package.json
│       └── boardgame.io/         # 官方示例仓库（参考）
├── templates/                    # 所有页面模板
├── static/
│   ├── bgio/                     # boardgame.io 构建产物
│   └── js/socket.io.min.js       # Socket.IO 客户端本地备份
├── tracker_api.py                # 物流追踪 API
├── tracker_scheduler.py          # 追踪调度服务（定时任务）
├── tracker_browser.py            # Playwright 抓取/解析逻辑
├── tools_api.py                  # 工具区文件上传/下载/缩略图
├── map/                          # 路线规划模块（Baidu Map API）
│   ├── map_api.py
│   ├── config.json
│   └── history.json
├── letter_league/                # 字母棋识别/推荐
│   ├── letter_api.py
│   └── twl06_ENABLE.txt           # 词库
├── weather/                      # 天气/能耗分析与图表输出
├── uploads/                      # 工具区上传目录
├── thumbnails/                   # 工具区缩略图目录
└── tracker.db                    # SQLite 追踪数据库
```

---

## 实时对战逻辑（/game/）

### 房间规则
- 房间 = 状态载体；玩家无身份系统，仅依赖 Socket 连接
- 房间最多 2 人，满员拒绝
- 最后 1 人离开后进入 10 分钟清理倒计时
- 房间可设置明文密码（加入时校验）

### 座位/颜色规则
- 座位 1 = 白方（playerID=0），座位 2 = 黑方（playerID=1）
- 自动分配空位；若某方已占用则不可切换
- 仅空位允许切换座位（前端禁用，后端校验）

### 悔棋规则
- 一方发起悔棋 → 对方弹窗确认
- 同意后仅撤回“最后一步执行者”的上一手
- 对方已走之后，上一手所有者才可悔棋

### boardgame.io 嵌入
- iframe 嵌入：`/static/bgio/index.html#/chess/multiplayer{playerID}?room={room_id}&embed=1`
- `matchID` 使用当前房间号（`room_id`）
- 每次加载棋盘附带 `v=timestamp` 缓存穿透

---

## API 端点（总览）

### 认证与管理
- `GET /api/auth/status`：查询登录状态
- `POST /api/auth/login`：登录（用户名/密码）
- `POST /api/auth/logout`：登出
- `GET /1/api/mijia_qr`：米家扫码二维码（管理员登录后）
- `POST /1/api/command`：记录管理员命令日志

### 系统与快捷动作
- `GET /api/system`：系统状态（CPU/内存/温度/WiFi/运行时间 + 历史）
- `POST /api/shortcut/run`：快捷动作
  - `append_reading`：写入暖气读数，触发 `weather/analyze_weather.py`
  - `get_latest`：获取最新读数

### 天气/能耗图表
- `GET /weather/<filename>`：天气/能耗图文件（含 SVG）
- `GET /weather_chart/<filename>`：同目录图表文件

### 工具区文件
- `POST /api/tools/upload`：上传文件
- `GET /api/tools/files`：列出文件 + 磁盘用量
- `POST /api/tools/delete/<name>`：删除文件（需登录）
- `GET /api/tools/download/<filename>`：下载文件
- `GET /uploads/<filename>`：直链访问上传文件
- `GET /thumbnails/<filename>`：直链访问缩略图

### 物流追踪
- `GET /api/tracker/list`：任务列表
- `POST /api/tracker/add`：添加任务
- `POST /api/tracker/toggle/<task_id>`：启用/停用
- `POST /api/tracker/run/<task_id>`：手动执行
- `POST /api/tracker/refresh/<task_id>`：刷新单个任务
- `POST /api/tracker/refresh_all`：刷新所有任务
- `POST /api/tracker/delete/<task_id>`：删除任务（需登录）
- `POST /api/tracker/delete_completed`：删除已完成任务（需登录）
- `GET /api/tracker/info/<task_id>`：任务详细路由信息

### 路线规划（Baidu Map）
- `GET /api/map/config` / `POST /api/map/config`：获取/保存配置
- `POST /api/map/geocode`：地理编码
- `POST /api/map/route`：路线规划 + 成本计算 + 交互式地图
- `GET /api/map/history`：历史记录
- `DELETE /api/map/history`：清空历史（需登录）
- `POST /api/map/map`：生成路线地图 HTML

### 字母棋识别
- `GET /letter`：字母棋识别页面
- `POST /api/letter/process`：上传图片并返回识别/推荐（流式响应）

### 实时对战（Socket.IO）
- `join_room`：加入房间（校验密码/满员）
- `leave_room`：离开房间
- `request_lobby`：获取大厅列表
- `set_room_password`：设置/清除房间密码
- `switch_seat`：切换白方/黑方（仅空位可切换）
- `request_undo`：申请悔棋
- `approve_undo` / `reject_undo`：同意/拒绝悔棋

---

## 关键配置与依赖

- Flask 密钥：`app.py` 中 `app.secret_key`
- 管理区限制：`ADMIN_PREFIX = /1`，仅局域网 IP（`192.168.178.0/24`）
- 米家登录：`AUTH_PATH = ~/.config/mijia-api/mijia-api-auth.json`
- 路线规划：`map/map_api.py` 内置 Baidu Map AK/SK
- 追踪数据库：`/home/bbdwz/projects/website/tracker.db`
- 追踪数据文件：`/home/bbdwz/projects/website/tracker_data_<id>.json`

---

## 构建与部署

### Flask 主站（生产）
- 服务：`website.service`
- 配置：`/etc/systemd/system/website.service`

### boardgame.io 服务
- 服务：`boardgame.service`
- 配置：`/etc/systemd/system/boardgame.service`
- 端口：`8000`（仅内网）

### 构建 boardgame.io 前端

```bash
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
cd /home/bbdwz/projects/website/game/boardgame/app
npm run build
cp -f dist/* /home/bbdwz/projects/website/static/bgio/
```

---

## 数据与文件输出

- `tracker.db`：追踪任务 SQLite
- `tracker_data_<id>.json`：每个追踪任务的独立结果
- `tracker_result.html`、`tracker_last.json`：抓取/解析临时与最后结果
- `weather/*.csv`、`weather/*.svg`：能耗数据与图表
- `map/history.json`：路线历史
- `map/config.json`：路线配置（油价/能耗）
- `uploads/`：工具区上传文件
- `thumbnails/`：工具区缩略图

---

## 日志位置

- Nginx 全局：`/var/log/nginx/error.log`, `/var/log/nginx/access.log`
- Nginx 站点：`/var/log/nginx/website_error.log`, `/var/log/nginx/website_access.log`
- Tracker 调度器：`/var/log/tracker.log`
- 管理员命令日志：`/home/bbdwz/admin_commands.log`
- 快捷动作日志：`/home/bbdwz/projects/website/shortcut.log`
- systemd 日志（查看）
  - `journalctl -u website.service -n 200 --no-pager`
  - `journalctl -u boardgame.service -n 200 --no-pager`
  - `journalctl -u tracker_scheduler.service -n 200 --no-pager`

---

## 运维命令

```bash
# 服务状态
systemctl status website.service
systemctl status boardgame.service
systemctl status tracker_scheduler.service

# 重启服务
sudo systemctl restart website.service
sudo systemctl restart boardgame.service
sudo systemctl restart tracker_scheduler.service

# Nginx 校验 & 重载
sudo nginx -t
sudo systemctl reload nginx
```

---

## 备注

- `/game/` 页面包含大厅 + 房间管理 + 棋盘嵌入
- 棋盘对局 ID 使用当前房间号（`matchID = room_id`）
- boardgame.io 更新后需重新构建并重启 `boardgame.service`

---

# 🪣 备份脚本（推荐）

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

使用说明（中文）：
- 运行脚本后会在 `/home/bbdwz/backups/` 下生成时间戳目录
- 目录内包含：网站源码、systemd 服务文件、Nginx 站点配置
- 恢复时可将对应文件复制回原位置，然后重启相关服务
