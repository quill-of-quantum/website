# 实时双人小游戏平台（Flask + Socket.IO）技术项目文档

## 1. 技术目标
- 基于 Flask 构建支持实时双人对战的 Web 游戏平台
- 支持多房间并发，多对玩家同时在线
- 使用 WebSocket（Socket.IO）实现低延迟通信
- 服务端作为唯一权威状态源，防止作弊
- 可运行于树莓派等低功耗设备
- 支持通过 HTTPS 内网穿透（如 cpolar）对公网访问

## 2. 技术栈选型
### 后端
- Python 3.x
- Flask
- Flask-SocketIO
- eventlet（异步引擎）
- Redis（可选）

### 前端
- HTML5
- JavaScript（原生）
- Socket.IO Client
- Canvas / SVG

### 通信
- HTTP / HTTPS
- WebSocket（Socket.IO，自动升级为 WSS）

## 3. 通信模型设计
### 3.1 HTTP 与 Socket.IO 职责划分
**HTTP**
- 页面加载
- 静态资源
- 初始房间创建 / 加入

**Socket.IO**
- 玩家操作
- 游戏状态同步
- 回合控制
- 房间内广播

### 3.2 WebSocket 建立流程
1. 浏览器通过 https 访问页面
2. Socket.IO 发送 HTTP 请求
3. Upgrade 为 WebSocket
4. 自动使用 wss 长连接

## 4. 服务端架构设计
### 4.1 模块结构
```
backend/
├── app.py
├── routes.py
├── socket_events.py
├── game/
│   ├── room.py
│   ├── base.py
│   ├── chess.py
│   └── billiards.py
└── utils/
    └── validator.py
```

### 4.2 Socket.IO 初始化
```python
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")
socketio.run(app, host="0.0.0.0", port=5000)
```

## 5. 房间系统设计
### 5.1 房间结构
```python
Room {
    room_id: str
    players: dict
    game: GameInstance
    state: waiting | playing | ended
}
```

### 5.2 设计原则
- 每局游戏一个房间
- 房间隔离
- 生命周期可控

## 6. 游戏逻辑设计
### 6.1 权威服务器模型
- 客户端仅发送操作意图
- 服务端校验并裁决

### 6.2 操作流程
客户端 → emit(action)
服务端 → 校验 → 更新状态 → 广播

## 7. Socket.IO 事件设计
| 事件 | 方向 | 说明 |
|----|----|----|
| connect | C→S | 连接 |
| join_room | C→S | 加入房间 |
| action | C→S | 游戏操作 |
| state_update | S→C | 状态同步 |
| game_over | S→C | 游戏结束 |

## 8. 前端架构
```
frontend/
├── templates/game.html
└── static/js/
    ├── socket.js
    ├── game.js
    └── render.js
```

## 9. 并发与性能
- 事件驱动
- 房间隔离
- 支持多房间并发

## 10. 异常处理
- 断线检测
- 非法操作忽略
- 房间延迟销毁

## 11. 可扩展性
- 游戏模块化
- 可接入 Redis / AI / 排行榜

## 12. 技术边界
- 不使用 WebRTC
- 单服务器实例优先

## 13. 推荐开发顺序
1. Socket.IO 通信
2. 房间系统
3. 基础棋类
4. 并发测试
5. 复杂游戏
