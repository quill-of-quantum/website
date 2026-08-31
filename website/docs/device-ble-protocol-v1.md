# ESP32-S3 BLE 通信协议 v1

BLE 是已批准设备的优先传输，Wi-Fi/HTTPS 是回退传输。未批准设备仍先用 Wi-Fi `register`，管理员批准且设备通过 Wi-Fi 取得 `ble_mode=preferred` 后，才开放 BLE 配对广播。

## 1. 角色与 GATT

- ESP32-S3：Peripheral + GATT Server。
- 树莓派：Central + GATT Client，`device-ble-gateway.service` 持续扫描。
- ESP32 广播必须包含 Service UUID。

```text
Service: 7f510001-1b15-4e3d-8d53-6f6f64777a01
Command: 7f510002-1b15-4e3d-8d53-6f6f64777a01  Pi -> ESP，Write + Write Without Response
Event:   7f510003-1b15-4e3d-8d53-6f6f64777a01  ESP -> Pi，Notify
```

建议使用 ESP-NimBLE、LE Secure Connections、bonding 和 NoInputNoOutput。Gateway 会请求 BlueZ OS pairing；应用层仍必须完成下面的 HMAC 身份认证。

## 2. 分帧

所有整数为 little-endian。每个 GATT value 的头部为 16 字节：

```c
typedef struct __attribute__((packed)) {
    uint8_t  magic[2];       // 'B', 'D'
    uint8_t  version;        // 1
    uint8_t  flags;          // 0x01 JSON；0x02 binary
    uint32_t message_id;
    uint16_t chunk_index;    // 从 0 开始
    uint16_t chunk_count;
    uint32_t crc32;          // 完整 payload 的 CRC32
} ble_frame_header_t;
```

单片 payload 大小：

```text
negotiated_mtu - 3 - 16
```

推荐协商 MTU 247。JSON 完整消息最大 64 KiB；BLE 照片默认最大 512 KiB。接收端按 `message_id` 重组，全部完成后验证完整 payload 的 CRC32。重复的相同分片可以忽略；相同序号但内容不同必须中止本次消息。

## 3. 会话认证

连接并订阅 Event 后，树莓派写入 Command：

```json
{"type":"challenge","protocol":1,"nonce":"32位hex","server_time":1788139000,"photo_max_bytes":524288}
```

设备计算：

```text
key   = SHA256(device_secret 原始字符串)
msg   = ASCII("bbdwz-ble-v1:" + nonce)
proof = hex(HMAC-SHA256(key, msg))
```

联调测试向量：

```text
device_secret = 0123456789abcdef0123456789abcdef
nonce         = aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
SHA256(secret)= 3eb1bd439947eb762998e566ccc2e099c791118b2f40579cc4f7da2b5061b7f9
proof         = 64d3211a45456dee5432e61e1bf976743622e5fdda23e397d606db0b0b0a5ddd
CRC32("123456789") = cbf43926
```

然后 Notify Event：

```json
{"type":"auth","request_id":"a1","device_id":"esp32s3-temp-c30a1d","nonce":"原nonce","proof":"64位hex"}
```

成功响应：

```json
{"type":"auth_ok","ok":true,"session_id":"随机值","server_time":1788139000,"config_version":13,"config":{}}
```

设备被删除或未批准时认证失败，ESP32 清除本地 approved/bonding 标志并回退 Wi-Fi register；不要删除 device identity 和待上传数据。

## 4. 普通消息

ESP32 发出的 JSON 都带唯一 `request_id`。服务器统一响应：

```json
{"type":"result","request_id":"原值","ok":true}
```

### Heartbeat

```json
{"type":"heartbeat","request_id":"h1","body":{"boot_reason":"timer_wakeup","config_version":12,"wifi_rssi":null,"free_heap_kb":8200}}
```

响应还包含 `config` 和 `config_version`，含义与 HTTPS heartbeat 完全一致。

### Status

```json
{"type":"status","request_id":"s1","body":{"config_version":12,"ble":{"bonded":true,"rssi":-53},"components":{"camera":{"enabled":true,"available":true,"code":"ok","model":"OV5640","resolution":"800x600"},"temperature_humidity":{"enabled":false,"code":"disabled"}}}}
```

status 的版本必须是本周期真正执行的版本；它同时是配置同步确认。

### Telemetry

```json
{"type":"telemetry","request_id":"t1","body":{"sample_id":"32位随机ID","sequence":12,"measured_at":"2026-08-31T12:30:00.123Z","data":{"temperature_c":23.4,"humidity_percent":56.2}}}
```

`sample_id` 必须与 Wi-Fi 重试使用同一个值，保证跨传输幂等。

## 5. BLE JPEG

先发送 JSON：

```json
{"type":"photo_begin","request_id":"p1","transfer_id":1001,"photo_id":"32位随机ID","captured_at":"2026-08-31T12:30:00.123Z","size_bytes":315724}
```

收到 `ready=true` 后，把 JPEG 原始字节作为一个 `flags=0x02, message_id=transfer_id` 的分片消息发送。服务器完成 CRC、长度和 JPEG 解码验证后返回最终结果。`ble-photo-disabled` 或 `ble-photo-too-large` 会同时返回 `wifi_required=true`，设备保留相同 `photo_id` 并回退 HTTPS photo。

## 6. 完成和回退

所有本周期必需数据均收到 `ok=true` 后：

```json
{"type":"done","request_id":"d1"}
```

收到 `complete=true` 后断开并 Deep Sleep。以下任一情况立即结束 BLE 并走 Wi-Fi：连接等待超时、pair/auth 失败、12 秒无响应、CRC 错误、任一必需数据未确认、服务器返回 `wifi_required=true`。

如果 Wi-Fi 成功而 BLE 尚未配对或本轮 BLE 失败，完成 HTTPS 任务后按照 `ble_pairing_timeout_sec` 再广播一次，供树莓派建立/修复 bonding；随后睡眠，不要无限等待。
