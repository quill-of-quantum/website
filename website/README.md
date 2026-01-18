# Raspberry Pi Flask 网站 + 实时对战平台

该项目运行在树莓派，主站为 Flask（Gunicorn + Nginx），并集成了 Socket.IO 实时房间系统和 boardgame.io 棋类对战服务。

---

## 功能概览

- 首页 / 工具区 / 追踪面板（Flask）
- 实时对战大厅 `/game/`（Socket.IO 房间管理）
- 棋类对战（boardgame.io 国际象棋嵌入 `/game/`）
- 后台追踪调度器（tracker_scheduler）

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

## 日志位置

- Nginx 全局：`/var/log/nginx/error.log`, `/var/log/nginx/access.log`
- Nginx 站点：`/var/log/nginx/website_error.log`, `/var/log/nginx/website_access.log`
- Tracker 调度器：`/var/log/tracker.log`
- 管理员命令日志：`/home/bbdwz/admin_commands.log`
- systemd 日志（查看）
  - `journalctl -u website.service -n 200 --no-pager`
  - `journalctl -u boardgame.service -n 200 --no-pager`
  - `journalctl -u tracker_scheduler.service -n 200 --no-pager`

---

## 目录结构（核心）

```
/home/bbdwz/projects/website/
├── app.py                        # Flask 主入口
├── game/
│   ├── game_api.py               # Socket.IO 房间逻辑
│   └── boardgame/
│       ├── app/                  # boardgame.io 源码 + 构建脚本
│       │   ├── server.js         # boardgame.io 服务端
│       │   ├── src/              # 前端源码（React）
│       │   └── package.json
│       └── boardgame.io/         # 官方示例仓库（参考）
├── templates/
│   ├── game.html                 # 实时大厅 + 棋盘嵌入页
│   └── ...
├── static/
│   ├── bgio/                     # boardgame.io 构建产物
│   └── js/socket.io.min.js       # Socket.IO 客户端本地备份
├── tracker_api.py                # 追踪接口
├── tracker_scheduler.py          # 调度服务
├── tracker.db                    # SQLite
└── ...
```

---

## 运行方式

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

## 常用运维命令

```bash
# 服务状态
systemctl status website.service
systemctl status boardgame.service
systemctl status tracker_scheduler.service

# 重启服务
sudo systemctl restart website.service
sudo systemctl restart boardgame.service

# Nginx 校验 & 重载
sudo nginx -t
sudo systemctl reload nginx
```

---

## 备注

- `/game/` 页面是大厅 + 房间管理 + 棋盘嵌入
- 棋盘对局 ID 使用当前房间号（`matchID = room_id`）
