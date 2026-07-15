# Raspberry Pi Flask 网站 + 实时对战平台

该项目运行在树莓派，主站为 Flask（Gunicorn + Nginx），集成 Socket.IO 实时房间系统和 boardgame.io 棋类对战服务，并包含工具区、物流追踪、路线规划、字母棋识别等模块。

---

## 功能概览

- 首页 / 云盘 / 剪贴板 / 视觉工具 / 追踪面板 / 路线规划 / 3D 预览（Flask）
- 聊天大厅 `/chat` 与实时对战大厅 `/game/`（Socket.IO 实时通信）
- 字母棋识别与推荐 `/letter`（OCR + GADDAG）
- 后台追踪调度器（tracker_scheduler）
- 系统监控、天气/能耗图表、花园记录、状态记录、极光信息

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
- `/cloud`：云盘页（上传/文件浏览/缩略图）（`templates/cloud.html`）
- `/clipboard`：网页剪贴板（`templates/tool_2.html`）
- `/chat`：聊天大厅（`templates/chat.html`）
- `/tracker`：物流追踪面板（`templates/tracker.html`）
- `/map`：路线规划页面（`templates/map.html`）
- `/route_creator`：路线创作页面（`templates/route_creator.html`）
- `/viewer`：3D 模型预览（`templates/viewer.html`）
- `/vision`：智能视觉检测页（`templates/tool_1.html`）
- `/game/` 与 `/game/<room_id>`：实时对战大厅与房间（`templates/game.html`）
- `/letter`：字母棋识别与推荐（`templates/letter.html`）
- `/garden` 与 `/garden/<garden_id>`：菜地记录页面（`templates/garden.html`）
- `/situation` 与 `/situation/map`：状态记录与地图（`templates/situation.html`, `templates/situation_map.html`）
- `/aurora/`：极光信息页面（`templates/aurora.html`）
- `/1/`：管理员面板（`templates/admin_index.html`）
- `/1/token`：App Token 管理（`templates/token.html`）

备注：`templates/login.html` 存在，但登录通过 `/api/auth/login` 进行。

---

## 模块与目录（详细）

```
/home/bbdwz/projects/website/
├── app.py                        # Flask 主入口（初始化与注册模块）
├── modules/                      # 后端功能模块
│   ├── index/api.py              # 首页与首页数据接口
│   ├── admin/api.py              # 管理后台
│   ├── auth/api.py               # 登录状态与登录/退出
│   ├── cloud/                    # 云盘上传/下载/缩略图
│   ├── tracker/                  # 物流追踪 API、抓取逻辑与调度器
│   ├── map/api.py                # 路线规划 API
│   ├── garden/api.py             # 菜地记录 API
│   ├── situation/api.py          # 状态记录 API
│   ├── sensor/api.py             # SGP30 传感器 API
│   ├── tools/                    # vision / clipboard 工具
│   │   └── models/               # 视觉模型文件（如 yolov8n.pt）
│   ├── game/api.py               # Socket.IO 房间/座位/悔棋逻辑
│   ├── chat/api.py               # Socket.IO 聊天大厅
│   ├── letter_league/api.py      # 字母棋识别/推荐
│   ├── aurora/api.py             # 极光信息
│   ├── weather/analyze.py        # 天气/能耗分析脚本
│   └── mail/api.py               # 邮件转发 API
├── data/                         # SQLite / JSON / 日志状态数据
│   ├── tracker/
│   ├── map/
│   ├── garden/
│   ├── situation/
│   ├── sensor/
│   ├── weather/
│   ├── tools/
│   ├── index/
│   ├── chat/
│   ├── aurora/
│   ├── admin/
│   ├── geoip/
│   └── route_creator/
├── storage/                      # 上传文件、用户文件与缩略图
│   ├── cloud/
│   └── vision/
├── logs/                         # 项目内运行日志
├── game/
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
├── letter_league/                # 字母棋词库与示例图片
├── tests/                        # 迁移后的测试/验证脚本
├── website_env_backup.yml        # Conda 环境备份
└── 树莓派连接局域网代理方法.txt      # 本机运维笔记
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

### 外部调用 API 返回规范

用于手机快捷指令、外部设备、脚本直接调用的接口统一返回：

成功：

```json
{
  "success": true,
  "message": "可选的人类可读提示",
  "data": {}
}
```

失败：

```json
{
  "success": false,
  "error": "错误原因",
  "code": "OPTIONAL_ERROR_CODE"
}
```

快捷指令只需要判断：

```txt
success 是 true
```

当前按此规范返回的外部接口：

- `POST /api/shortcut/run`
- `POST /api/cloud/upload`
- `POST /api/situation`

示例：`POST /api/cloud/upload`

```json
{
  "success": true,
  "message": "✅ 文件已上传：IMG_6933.jpeg",
  "data": {
    "filename": "uuid.jpeg",
    "stored_name": "uuid.jpeg",
    "original_name": "IMG_6933.jpeg",
    "size": 2399329
  }
}
```

### 认证与管理
- `GET /api/auth/status`：查询登录状态
- `POST /api/auth/login`：登录（用户名/密码）
- `POST /api/auth/logout`：登出
- `GET /1/api/mijia_qr`：米家扫码二维码（管理员登录后）
- `POST /1/api/command`：记录管理员命令日志

### 系统与快捷动作
- `GET /api/system`：系统状态（CPU/内存/温度/WiFi/运行时间 + 历史）
- `POST /api/shortcut/run`：快捷动作
  - `append_reading`：写入暖气读数，触发 `modules/weather/analyze.py`
  - `get_latest`：获取最新读数

### 天气/能耗图表
- `GET /weather/<filename>`：天气/能耗 CSV 或 SVG 文件
- `GET /weather_chart/<filename>`：兼容旧图表 URL

### 云盘文件
- `POST /api/cloud/upload`：上传文件
- `GET /api/cloud/files`：列出文件 + 磁盘用量
- `POST /api/cloud/delete/<name>`：删除文件（需登录）
- `GET /api/cloud/download/<filename>`：下载文件
- `POST /api/cloud/clean_thumbnails`：清理孤立缩略图（需登录）
- `GET /uploads/<filename>`：直链访问上传文件
- `GET /thumbnails/<filename>`：直链访问缩略图

### 工具
- `GET /clipboard`：剪贴板页面
- `GET /api/clipboard` / `POST /api/clipboard`：读取/更新剪贴板
- `POST /api/vision/upload`：上传视觉检测图片
- `POST /api/vision/analyze`：运行视觉检测

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
- `GET /api/map/reverse_geocode`：逆地理编码
- `POST /api/map/geocode`：地理编码
- `POST /api/map/topo`：地形/海拔数据
- `POST /api/map/route`：路线规划 + 成本计算 + 交互式地图
- `GET /api/map/history`：历史记录
- `DELETE /api/map/history`：清空历史（需登录）
- `GET /api/map/favorites` / `POST /api/map/favorites`：收藏路线
- `DELETE /api/map/favorites/<fav_id>`：删除收藏
- `GET /api/map/favorite_images/<filename>`：收藏路线图片
- `POST /api/map/map`：生成路线地图 HTML

### 路线创作
- `GET /api/route_creator/list`：路线草稿列表
- `POST /api/route_creator/save`：保存路线草稿
- `GET /api/route_creator/get/<route_id>`：获取路线草稿
- `POST /api/route_creator/match`：匹配手绘路线
- `GET /api/route_creator/progress`：匹配进度
- `GET /api/route_creator/result`：匹配结果

### 花园与状态记录
- `GET /api/garden/list`：菜地列表
- `POST /api/garden/create`：创建菜地
- `POST /api/garden/delete/<garden_id>`：删除菜地
- `GET /api/garden/state` / `GET /api/garden/state/<garden_id>`：读取菜地状态
- `POST /api/garden/change/<garden_id>`：记录菜地变更
- `GET /api/garden/plants/search`：搜索植物
- `POST /api/situation`：记录状态
- `GET /api/situation/latest`：最新状态
- `GET /api/situation/settings`：状态设置
- `GET /api/situation/track`：状态轨迹
- `GET /api/situation/list`：状态记录列表

### 传感器、极光与邮件
- `GET /api/sensor/latest`：最新 SGP30 数据
- `GET /api/sensor/log`：SGP30 历史日志
- `GET /aurora/api/status`：极光/Kp 实时状态
- `GET /aurora/api/forecast`：Kp 趋势
- `GET /aurora/api/location_estimate`：位置估算
- `GET /aurora/api/ovation`：OVATION 极光数据
- `GET /aurora/api/social`：社交/外部信息
- `GET /aurora/api/location` / `POST /aurora/api/location`：读取/保存观测位置
- `POST /api/mail/send`：转发邮件发送请求

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
- GeoIP 城市库：`data/geoip/GeoLite2-City.mmdb`
- 米家登录：`AUTH_PATH = ~/.config/mijia-api/mijia-api-auth.json`
- 路线规划：`modules/map/api.py` 内置 Baidu Map AK/SK
- 视觉检测模型：`modules/tools/models/yolov8n.pt`
- 追踪数据库：`/home/bbdwz/projects/website/data/tracker/tracker.db`
- 追踪数据文件：`/home/bbdwz/projects/website/data/tracker/tracker_data_<id>.json`
- 追踪调度器：`tracker_scheduler.service` 直接启动 `modules/tracker/scheduler.py`
- 天气分析脚本：`modules/weather/analyze.py`
- Git 忽略规则：`/home/bbdwz/projects/.gitignore` 统一管理 website、gallery、email-service 等项目的运行数据忽略规则

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

- `data/tracker/tracker.db`：追踪任务 SQLite
- `data/tracker/tracker_data_<id>.json`：每个追踪任务的独立结果
- `data/tracker/tracker_result.html`、`data/tracker/tracker_last.json`：抓取/解析临时与最后结果
- `data/weather/*.csv`、`data/weather/number.txt`：能耗原始数据与 CSV 数据
- `data/weather/*.svg`：能耗图表输出
- `data/index/exchange_rate.json`：首页汇率走势后台缓存
- `data/chat/lobby.json`：聊天大厅设备身份、头像与消息记录
- `data/map/history.json`：路线历史
- `data/map/config.json`：路线配置（油价/能耗）
- `data/map/favorites.json`、`data/map/favorite_images/`：路线收藏与图片
- `data/map/geocode_cache.db`、`data/map/cache/`：地理编码与地形缓存
- `data/garden/*.json`、`data/garden/events.jsonl`：菜地状态与事件
- `data/situation/situation_log.txt`、`data/situation/site.txt`：状态记录数据
- `data/sensor/sgp30.log`：SGP30 传感器日志
- `data/aurora/selected_location.json`：极光观测位置
- `data/admin/app_tokens.json`：App API Token 哈希与元数据
- `data/geoip/GeoLite2-City.mmdb`：管理区访问统计的 IP 地理位置库
- `data/tools/clipboard.txt`：网页剪贴板内容
- `storage/cloud/uploads/`：云盘上传文件
- `storage/cloud/thumbnails/`：云盘缩略图
- `storage/vision/uploads/`：视觉工具临时上传文件
- `data/map/output/`：路线规划测试/生成产物
- `data/route_creator/`：路线创作草稿、OSM 数据与匹配相关数据

---

## 日志位置

- Nginx 全局：`/var/log/nginx/error.log`, `/var/log/nginx/access.log`
- Nginx 站点：`/var/log/nginx/website_error.log`, `/var/log/nginx/website_access.log`
- Tracker 调度器：`/var/log/tracker.log`
- 管理员命令日志：`/home/bbdwz/admin_commands.log`
- 快捷动作日志：`/home/bbdwz/projects/website/logs/shortcut.log`
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
