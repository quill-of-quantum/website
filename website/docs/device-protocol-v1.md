# ESP32-S3 外设通信协议 v1

状态：草案，可用于第一块温湿度设备开发。

目标：所有 ESP32-S3 外设使用同一套注册、认证、配置、心跳和数据上报规则；设备类型只决定 `telemetry` 和 `capabilities` 中的数据，不改变基础协议。

## 1. 传输层

- 主通道：Wi-Fi Station + HTTPS `POST/GET`。
- 服务器入口：当前使用 cpolar HTTPS 地址；固件将其作为可更新的 `server_base_url` 保存。
- URL 不使用 IP 写死，必须支持域名和路径前缀。
- HTTP 超时：连接 10 秒、读写 20 秒；摄像头上传可单独使用 120 秒。
- 每次唤醒只建立一次 Wi-Fi/TLS 会话，完成上报和配置同步后关闭无线并睡眠。
- TLS 必须校验证书链和主机名；不允许“忽略证书错误”。

基础 URL：

```text
{server_base_url}/api/device/v1
```

## 2. 设备身份

设备第一次启动时在 NVS 中生成并持久化：

```text
device_id       3–80 个 ASCII 字符；首字符为字母或数字，后续仅允许字母、数字、`.`、`_`、`:`、`-`
device_secret   至少 32 字节随机值，只保存在设备和服务器哈希中
```

`device_secret` 不得写入日志、网页或普通遥测数据。

推荐设备 ID 示例：

```text
esp32s3-temp-a13f82
esp32s3-cam-7b9210
```

服务器只保存 `SHA-256(device_secret)`，不保存明文设备密钥。

## 3. 配对和注册

### 3.1 打开配对窗口

管理员在 `/1/devices` 点击“添加新设备”，服务器打开配对模式。配对模式不会自动超时，必须由管理员点击“停止等待”手动关闭。模式关闭时，新设备注册请求返回：

```json
{"ok":false,"error":"pairing-not-open"}
```

已批准设备不受配对窗口影响，可以正常重新上线。

### 3.2 注册请求

```http
POST /api/device/v1/register
Content-Type: application/json
```

```json
{
  "device_id": "esp32s3-temp-a13f82",
  "device_secret": "设备本地随机密钥",
  "name": "客厅温湿度",
  "device_type": "temperature",
  "firmware": "0.1.0",
  "capabilities": ["temperature", "humidity", "battery"],
  "metadata": {
    "chip": "ESP32-S3",
    "board": "待填写具体开发板",
    "flash_mb": 8,
    "psram_mb": 8
  }
}
```

首次请求只会创建 `pending` 设备，不会获得数据读取或配置权限。服务器返回：

```json
{
  "ok": true,
  "status": "pending",
  "device_id": "esp32s3-temp-a13f82",
  "config": {},
  "config_version": 0
}
```

管理员批准后，设备再次注册或发送心跳即可变为正常通信状态。设备断电重启后重复注册不会产生重复设备。

管理员删除设备后，服务器不保留禁用记录。该设备的 heartbeat、telemetry 和 config 请求返回：

```json
{"ok":false,"error":"device-not-found"}
```

设备收到后必须把本地 `approved` 改为 false，但保留原 `device_id` 和 `device_secret`。下次唤醒重新调用 register；只有管理员打开“添加新设备”后才能再次进入待批准列表。

删除设备只删除连接身份，不删除历史数据。该次注册对应的数据包会在 `/1/devices/database` 中标记为“设备已删除”，直到管理员手动删除某类数据或整个数据包。

## 4. 已批准设备认证

设备 API 使用请求头认证：

```http
X-Device-ID: esp32s3-temp-a13f82
X-Device-Secret: 设备本地随机密钥
X-Device-Transport: wifi/cpolar
```

后续可以升级为设备公钥签名，但 v1 先使用随机设备密钥 + HTTPS。密钥不匹配返回：

```json
{"ok":false,"error":"unauthorized"}
```

设备不能使用管理员 Session、主站用户密码或 App Token 调用设备 API。

## 5. 心跳和配置同步

```http
POST /api/device/v1/heartbeat
Content-Type: application/json
X-Device-ID: ...
X-Device-Secret: ...
X-Device-Transport: wifi/cpolar
```

请求体用于上报本轮设备状态，字段可以按能力扩展：

```json
{
  "boot_reason": "timer_wakeup",
  "config_version": 3,
  "wifi_rssi": -42,
  "battery_percent": 87,
  "free_heap_kb": 241
}
```

成功返回：

```json
{
  "ok": true,
  "status": "approved",
  "server_time": 1787870000,
  "config": {
    "sample_interval_sec": 300,
    "upload_enabled": false,
    "photo_enabled": false,
    "camera_resolution": "800x600",
    "jpeg_quality": 12,
    "ble_mode": "maintenance"
  },
  "config_version": 4
}
```

设备保存配置后，必须记录本地 `config_version`。服务器只递增版本号，不原地覆盖版本号。

`heartbeat` 请求中的 `config_version` 只表示“设备当前保存的版本”，不能作为配置同步确认。当前固件以 `/status` 中的版本和硬件结果表示“本周期确实按此版本运行”，因此 status 同时作为配置同步确认。设备按以下顺序完成一次配置同步：

1. `heartbeat` 拉取服务器配置；
2. 校验、应用并提交 NVS；
3. 下一次唤醒时按新配置初始化或关闭硬件，并完成采样、拍照和本轮指示灯动作；
4. `POST /status` 上报本周期实际使用的配置版本和组件检测结果，服务器此时标记为已同步。

特别注意：如果 heartbeat 在本周期取回版本 8，但本周期采样/拍照使用的仍是版本 7，随后 status 必须报告 `config_version: 7`；不能因为版本 8 已写入 NVS 就提前报告 8。下一次唤醒真正按版本 8 执行后，才能在 status 报告 8。

`/config/ack` 作为未来固件的可选兼容接口保留，当前固件不需要调用：

```http
POST /api/device/v1/config/ack
Content-Type: application/json
X-Device-ID: ...
X-Device-Secret: ...
X-Device-Transport: wifi/cpolar
```

```json
{"config_version":4}
```

当前固件的 status 版本与服务器当前版本完全相等时，后台显示“已同步到设备”。heartbeat 即使报告相同或更高版本也不会触发同步。

如果未来固件同时调用 ACK，服务器会检查设备是否已经提交当前版本的 `/status`：未提交时 ACK 返回 HTTP 409 `config-status-required`；ACK 版本落后于服务器时返回 HTTP 409 `stale-config-version`。

推荐配置字段：

```json
{
  "sample_interval_sec": 300,
  "upload_enabled": false,
  "photo_enabled": false,
  "camera_resolution": "800x600",
  "jpeg_quality": 12,
  "wifi_retry_count": 2,
  "wifi_retry_backoff_sec": 60,
  "ble_mode": "off",
  "ble_advertise_sec": 30
}
```

设备不能执行配置中的任意命令；配置只能是固件明确支持的字段。

`sample_interval_sec` 允许范围为 5–86400 秒。低于 5 秒不适合当前 Deep-sleep 工作流，也会被服务器拒绝。

ESP32-S3 固件必须在读取 NVS 和应用服务器配置的两处校验中同样允许 `>= 5`；否则服务器虽保存成功，固件仍会忽略 5–29 秒的配置。

后台不允许编辑任意配置 JSON。服务器根据 `device_type` 返回预定义字段并验证类型和范围。v1 预设 `generic`、`temperature`、`camera`、`environment`、`switch`，未知类型使用 `generic`。

新注册设备的温湿度采集和周期拍照默认均为关闭。`upload_enabled=true` 表示初始化温湿度传感器、采集并上传；`photo_enabled=true` 表示初始化摄像头、拍摄并上传。关闭时固件不得初始化对应硬件。

OV5640 可选分辨率与 ESP Camera 枚举对应：

```text
320x240    FRAMESIZE_QVGA
640x480    FRAMESIZE_VGA
800x600    FRAMESIZE_SVGA
1024x768   FRAMESIZE_XGA
1280x720   FRAMESIZE_HD
1600x1200  FRAMESIZE_UXGA
1920x1080  FRAMESIZE_FHD
2048x1536  FRAMESIZE_QXGA
2560x1920  FRAMESIZE_QSXGA
```

`jpeg_quality` 使用 `esp32-camera` 的 5–63 范围，数值越小质量越高，默认 12。

## 5.1 硬件检测结果

设备应用配置并检测硬件后，在同一唤醒周期立即上报：

```http
POST /api/device/v1/status
Content-Type: application/json
X-Device-ID: ...
X-Device-Secret: ...
X-Device-Transport: wifi/cpolar
```

```json
{
  "config_version": 5,
  "components": {
    "temperature_humidity": {
      "enabled": true,
      "available": false,
      "code": "not-found"
    },
    "camera": {
      "enabled": true,
      "available": true,
      "model": "OV5640",
      "resolution": "800x600"
    }
  }
}
```

统一含义：

- 配置关闭：`enabled=false, code="disabled"`，不探测硬件，并建议省略 `available`。
- 配置打开且初始化/读取成功：`enabled=true, available=true`。
- 配置打开但未检测到对应硬件：`enabled=true, available=false, code="not-found"`。
- 其他错误使用稳定短代码，如 `init-failed`、`capture-failed`、`read-failed`，详细信息可放 `detail`，不得包含密钥。

后台根据这个结构实时显示“已关闭 / 已连接 / 无设备”。新组件继续在 `components` 中增加键，不需要新增状态接口。

## 6. 通用数据上报

```http
POST /api/device/v1/telemetry
Content-Type: application/json
X-Device-ID: ...
X-Device-Secret: ...
X-Device-Transport: wifi/cpolar
```

建议格式：

```json
{
  "sample_id": "a1b2c3d4",
  "measured_at": "2026-08-27T12:30:00Z",
  "sequence": 182,
  "battery_percent": 87,
  "data": {
    "temperature_c": 23.4,
    "humidity_percent": 56.2
  }
}
```

通用字段保持不变，设备专用字段放在 `data` 中：

```json
{"data":{"co2_ppm":612,"pressure_pa":100812}}
```

服务器把每次数据按设备注册实例写入统一 SQLite 数据包，并按温度、湿度、电量及其他字段分类。`sample_id` 应由设备生成；同一个 `sample_id + 字段` 重试不会重复记录。

成功响应：

```json
{
  "ok": true,
  "received": true,
  "server_time": 1787870000,
  "config": {},
  "config_version": 4
}
```

## 7. 摄像头上传

```http
POST /api/device/v1/photo
Content-Type: image/jpeg
X-Device-ID: ...
X-Device-Secret: ...
X-Device-Transport: wifi/cpolar
X-Photo-ID: 32位小写十六进制随机ID
X-Captured-At: 2026-08-30T12:30:00.123Z
```

限制：

- 请求体直接是 JPEG 二进制，不使用 JSON 或 Base64。
- 只接受 `Content-Type: image/jpeg`，最大 8 MiB。
- 服务器完整解码 JPEG，并记录宽度、高度、字节数、拍摄时间和存储路径。
- 图片按设备和 UTC 日期分目录保存；元数据与传感器读数写入同一个 SQLite 数据库，并使用 `(设备数据包, photo_id)` 唯一约束。
- `X-Photo-ID` 必须匹配 `[0-9a-f]{32}`。同一照片重试返回成功和 `duplicate=true`，不会重复写入。
- `X-Captured-At` 必须是带时区的 ISO 8601。设备没有可信 UTC 时可以省略，服务器使用接收时间并标记来源。

成功和重复上传使用相同响应结构，始终返回完整当前配置：

```json
{
  "ok": true,
  "photo_id": "a13f82c7e9124c6e87964392c827f321",
  "duplicate": false,
  "photo": {
    "width": 800,
    "height": 600,
    "size_bytes": 82431,
    "captured_at": "2026-08-30T12:30:00.123Z"
  },
  "config": {
    "photo_enabled": true
  },
  "config_version": 4
}
```

`photo_enabled` 属于 `temperature` Profile，必须是布尔值。`true` 表示每个采样周期拍照并上传；`false` 表示本周期完全不初始化摄像头。图片上传与温湿度的 `upload_enabled` 相互独立，二者对新设备都默认关闭。

## 7.1 统一数据保存与删除

- 数据库：`data/devices/device_data.sqlite3`。
- 照片文件：`data/devices/photos/<device_id>/<UTC日期>/<photo_id>.jpg`。
- 一个数据包对应一次设备注册身份（内部 `packet_id`），包含照片、温度、湿度等分类。
- 删除设备不会删除数据包；设备使用同一 ID 重新配对也会建立新的注册数据包。
- `/1/devices/database` 可以删除某一分类或整个数据包。删除照片分类或数据包时，对应 JPEG 文件也会删除。

## 8. 错误和重试

设备只根据稳定错误码决定行为：

```text
pairing-not-open       等待下一次唤醒，不持续重试
device-not-found       服务器没有保存该设备；清除本地 approved 后重新注册
device-pending         设备已登记但尚未被管理员批准
unauthorized           停止上传，保留本地数据
server-busy            退避重试
invalid-payload        修正固件数据后再发送
```

推荐退避：15 秒、60 秒、5 分钟；达到次数后保存本地数据并进入 Deep-sleep。

## 8.1 设备状态机和 HTTP 状态

```text
POST register
  200 pending            已进入待批准列表
  200 approved           已绑定设备重新注册成功
  403 pairing-not-open   服务器未开放添加设备
  401 unauthorized       相同 device_id 的密钥不匹配

POST heartbeat / status / telemetry / photo，GET config
  200                    已批准，通信成功
  404 device-not-found   服务器没有保存该设备
  409 device-pending     设备存在但尚未批准
  401 unauthorized       设备密钥不匹配
```

收到 `device-not-found` 或 `device-pending` 时，固件必须设置 `approved=false` 并持久化，但不得删除或重新生成设备身份。下一轮改走 register。

## 9. 低功耗状态机

```text
BOOT
  → WIFI_CONNECT
  → REGISTER 或 HEARTBEAT
  → CONFIG_SYNC
  → 仅初始化已开启的组件
  → READ_SENSOR / CAPTURE_IMAGE
  → STATUS（报告已连接/无设备）
  → TELEMETRY / PHOTO
  → WIFI_OFF
  → DEEP_SLEEP
```

温湿度设备默认每 5 分钟唤醒；摄像头默认每 15 分钟唤醒。设备不上电时服务器不能主动唤醒设备，后台配置会在设备下一次上线时应用。

BLE 维护模式只在配置的时间窗口广播，默认 30 秒；不建议电池设备长期保持 Wi-Fi 或 BLE 连接。

## 10. BLE 预留协议

BLE 使用一个自定义 128-bit GATT Service。v1 预留以下 Characteristic：

```text
status       Read/Notify   设备状态、电量、固件版本
config       Read/Write    配置 JSON
command      Write         有限命令，如 trigger_sample、trigger_photo
result       Notify        命令结果
```

BLE 写入配置必须经过配对和加密；`command` 只允许固件白名单命令，不接受 shell 或脚本。

## 11. 开发板信息清单

开发固件前请确认并记录：

```text
ESP32-S3 具体芯片/模组型号：例如 ESP32-S3-WROOM-1
开发板完整型号：
Flash 容量：
PSRAM 容量和类型：
天线型号：板载/外置
温湿度传感器型号与 I²C 地址：
摄像头型号：OV2640/OV3660/其他
摄像头接口：并口/SPI/其他
电池型号、容量和供电电压：
电池电压 ADC 引脚及分压电阻：
传感器电源控制 GPIO：
摄像头电源控制 GPIO：
唤醒按键或外部唤醒 GPIO：
状态 LED GPIO：
```

不同 ESP32-S3 CAM 板的摄像头 GPIO 映射可能完全不同，不能只按“ESP32-S3 CAM”这个名称写死。

## 12. 实现顺序

1. 用模拟请求验证注册、批准、删除、重新注册和配置版本。
2. 用 ESP32-S3 温湿度设备实现 Deep-sleep、注册、上报和配置同步。
3. 加入 BLE 维护模式。
4. 增加摄像头图片上传。
5. 后续再加入设备公钥签名、OTA 和历史数据查询。
