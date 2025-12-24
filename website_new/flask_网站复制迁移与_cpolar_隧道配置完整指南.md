# Flask 网站复制迁移与 cpolar 隧道配置（完整可复用版）

> 目标：
>
> - **在不影响现有网站的情况下**，复制一套结构
> - 新网站可通过 **cpolar 单独隧道访问**
> - 命名清晰、可控，不使用随意名称
> - 保持你现在的 **Gunicorn + systemd + Nginx + cpolar** 架构

---

## 一、先明确你现在已有的「基准网站」

为了避免混乱，下面先定义 **你已经在运行的网站（基准）**。

> ⚠️ 以下名称 **不是让你改的**，而是用于对照

| 层级              | 当前网站（基准）示例                     |
| --------------- | ------------------------------ |
| 项目目录            | `/home/bbdwz/projects/website` |
| systemd service | `website.service`              |
| Gunicorn socket | `/home/bbdwz/website.sock`     |
| Nginx 监听端口      | `80`                           |
| cpolar tunnel   | `index_web → addr: 80`         |

---

## 二、迁移前：先“设计”新网站的命名（你必须确认）

在动任何文件前，请**先决定下面 5 个名字**（强烈建议写下来）：

### 1️⃣ 新网站的「逻辑名称」（人脑识别用）

例如：

- `dashboard`
- `admin`
- `demo`
- `monitor`

> **假设下面示例统一使用：**``

---

### 2️⃣ 新项目目录（必须唯一）

```text
/home/bbdwz/projects/dashboard
```

---

### 3️⃣ systemd service 名称

```text
dashboard.service
```

规则：

- 小写
- 不加数字
- 与项目含义一致

---

### 4️⃣ Gunicorn socket 文件

```text
/home/bbdwz/dashboard.sock
```

规则：

- 与 service / 项目同名
- 避免 `website-2.sock` 这种不可读命名

---

### 5️⃣ 对外访问方式

**端口方式（推荐，与你现在一致）：**

| 项目         | 示例                     |
| ---------- | ---------------------- |
| Nginx 端口   | `81`                   |
| cpolar 子域名 | `dashboard.cpolar.top` |

---

## 三、步骤 1：复制项目目录（不动原网站）

```bash
cp -r /home/bbdwz/projects/website /home/bbdwz/projects/dashboard
```

然后进入新目录，确认 Flask 入口文件存在，例如：

```bash
ls /home/bbdwz/projects/dashboard
# app.py / wsgi.py / requirements.txt ...
```

> ⚠️ 如果你希望是“全新逻辑”，可以在这一步修改 Flask 代码

---

## 四、步骤 2：创建新的 systemd service

### 1️⃣ 新建 service 文件

```bash
sudo nano /etc/systemd/system/dashboard.service
```

---

### 2️⃣ **完整示例（可直接用）**

```ini
[Unit]
Description=Gunicorn service for dashboard website
After=network.target

[Service]
User=bbdwz
Group=www-data

WorkingDirectory=/home/bbdwz/projects/dashboard

Environment="PATH=/home/bbdwz/miniconda3/envs/web/bin"

ExecStart=/home/bbdwz/miniconda3/envs/web/bin/gunicorn \
    -w 3 \
    -k gevent \
    --timeout 180 \
    -b unix:/home/bbdwz/dashboard.sock \
    app:app

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> ⚠️ 如果你原来不是 `app:app`，请与原 `website.service` 保持一致

---

### 3️⃣ 让 systemd 重新加载配置

```bash
sudo systemctl daemon-reload
```

---

### 4️⃣ 启动新网站（旧网站不会动）

```bash
sudo systemctl start dashboard.service
```

验证：

```bash
systemctl status dashboard.service
```

---

## 五、步骤 3：为新网站配置 Nginx（新端口）

### 1️⃣ 复制现有配置

```bash
sudo cp /etc/nginx/sites-available/website /etc/nginx/sites-available/dashboard
```

---

### 2️⃣ 编辑新配置

```bash
sudo nano /etc/nginx/sites-available/dashboard
```

示例（关键只改 2 行）：

```nginx
server {
    listen 81;

    location / {
        proxy_pass http://unix:/home/bbdwz/dashboard.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

### 3️⃣ 启用该站点

```bash
sudo ln -s /etc/nginx/sites-available/dashboard /etc/nginx/sites-enabled/
```

---

### 4️⃣ 测试并重载 Nginx

```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

## 六、步骤 4：在 cpolar.yml 中添加新 tunnel

你当前已有：

```yaml
tunnels:
  raspberry_ssh:
    proto: tcp
    addr: "22"
    region: eu

  index_web:
    proto: http
    addr: "80"
    region: eu
    subdomain: ggg
```

### ➕ 新增 dashboard tunnel（同级）

```yaml
  dashboard_web:
    proto: http
    addr: "81"
    region: eu
    subdomain: dashboard
```

⚠️ 规则：

- **不要重复写 **``
- `addr` 对应的是 **Nginx 端口**
- 名称与网站语义一致

---

### 重启 cpolar（仍然只有一个进程）

```bash
cpolar start
```

---

## 七、最终结构总览（你应该看到的状态）

```text
Flask
 ├── website     → website.sock
 └── dashboard   → dashboard.sock

Gunicorn (systemd)
 ├── website.service
 └── dashboard.service

Nginx
 ├── :80 → website.sock
 └── :81 → dashboard.sock

cpolar（1 个进程）
 ├── ggg.cpolar.top        → :80
 └── dashboard.cpolar.top  → :81
```

---

## 八、日常维护你只需要记住 3 条命令

```bash
# 看日志
journalctl -u dashboard.service -f

# 重启单个网站
sudo systemctl restart dashboard.service

# 检查 Nginx
sudo nginx -t
```

---

## 九、如果你要再复制第 3 / 第 4 个网站

只需重复：

- **命名确认（目录 / service / socket / 端口 / 子域）**
- 不要改已有任何文件

> 这套结构是 **线性可扩展的**

---

> 如果你愿意，下一步我可以：
>
> - 帮你把这个流程压缩成一页「操作清单」
> - 或按你给的名字，直接生成 **定制版配置**

