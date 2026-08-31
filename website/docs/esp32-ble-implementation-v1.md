# ESP32-S3 BLE 优先传输实现结构

适用：ESP-IDF 5.5.x、ESP32-S3、NimBLE、Deep Sleep。完整报文字段和 UUID 以 `device-ble-protocol-v1.md` 为准。

## 1. menuconfig

```text
Component config
  Bluetooth
    Bluetooth = enabled
    Host = NimBLE
    Controller = enabled
    BLE only = enabled
    Maximum connections = 1
  Wi-Fi
    Software controls WiFi/Bluetooth coexistence = enabled
```

只使用 BLE，不要启用 Classic Bluetooth。启用 NimBLE bonding 的持久存储。

## 2. 配置和本周期快照

现有 NVS 增加：

```c
typedef enum {
    BLE_MODE_OFF = 0,
    BLE_MODE_PREFERRED = 1,
} device_ble_mode_t;

typedef struct {
    device_ble_mode_t mode;
    uint32_t attempt_timeout_sec;       // 默认 3
    uint32_t pairing_timeout_sec;       // 默认 10
    bool photo_enabled;                 // BLE 是否允许传照片
    uint32_t photo_max_bytes;           // 最大 524288
    bool bonded;                        // 本地已完成过认证/配对
} device_ble_config_t;

typedef struct {
    uint32_t config_version;             // 本周期实际执行的版本
    uint32_t sequence;
    bool upload_enabled;
    bool photo_enabled;
    char camera_resolution[16];
    int jpeg_quality;
    sensor_sample_t sample;
    uint8_t *jpeg_data;
    size_t jpeg_size;
    char sample_id[33];
    char photo_id[33];
    char measured_at[40];
} cycle_snapshot_t;
```

`app_main()` 读取 NVS 后、请求服务器之前立即生成 `cycle_snapshot_t`。heartbeat 收到的新配置只能更新全局/NVS，不能修改这个快照。

## 3. 总状态机

```c
typedef enum {
    TRANSPORT_OK,
    TRANSPORT_NOT_ENABLED,
    TRANSPORT_CONNECT_FAILED,
    TRANSPORT_AUTH_FAILED,
    TRANSPORT_TIMEOUT,
    TRANSPORT_WIFI_REQUIRED,
    TRANSPORT_PROTOCOL_ERROR,
} transport_result_t;

void app_main(void)
{
    initialise_nvs();
    load_identity_and_config();

    cycle_snapshot_t cycle = build_cycle_snapshot();
    collect_enabled_sensor_data(&cycle);
    capture_enabled_photo(&cycle);

    transport_result_t ble_result = TRANSPORT_NOT_ENABLED;

    if (s_is_approved && s_ble_config.mode == BLE_MODE_PREFERRED) {
        ble_result = ble_transport_run(&cycle,
            s_ble_config.bonded
                ? s_ble_config.attempt_timeout_sec
                : s_ble_config.pairing_timeout_sec);
    }

    if (ble_result == TRANSPORT_OK) {
        release_cycle_buffers(&cycle);
        prepare_deep_sleep();
        return;
    }

    bool wifi_ok = wifi_https_transport_run(&cycle);

    if (wifi_ok && s_ble_config.mode == BLE_MODE_PREFERRED &&
        (!s_ble_config.bonded || ble_result != TRANSPORT_NOT_ENABLED)) {
        /* HTTPS 已完成后再给树莓派一次配对/修复机会。 */
        ble_pairing_only_run(s_ble_config.pairing_timeout_sec);
    }

    release_cycle_buffers(&cycle);
    prepare_deep_sleep();
}
```

`ble_transport_run()` 只有在 heartbeat、status、需要的 telemetry、需要的 photo 和 done 全部获得确认后才返回 `TRANSPORT_OK`。

## 4. GATT Server

```c
#include "host/ble_hs.h"
#include "host/ble_gap.h"
#include "host/ble_gatt.h"
#include "host/ble_store.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "store/config/ble_store_config.h"

static const ble_uuid128_t SERVICE_UUID =
    BLE_UUID128_INIT(0x01,0x7a,0x77,0x64,0x6f,0x6f,0x53,0x8d,
                     0x3d,0x4e,0x15,0x1b,0x01,0x00,0x51,0x7f);
static const ble_uuid128_t COMMAND_UUID =
    BLE_UUID128_INIT(0x01,0x7a,0x77,0x64,0x6f,0x6f,0x53,0x8d,
                     0x3d,0x4e,0x15,0x1b,0x02,0x00,0x51,0x7f);
static const ble_uuid128_t EVENT_UUID =
    BLE_UUID128_INIT(0x01,0x7a,0x77,0x64,0x6f,0x6f,0x53,0x8d,
                     0x3d,0x4e,0x15,0x1b,0x03,0x00,0x51,0x7f);

static uint16_t s_event_value_handle;
static uint16_t s_connection_handle = BLE_HS_CONN_HANDLE_NONE;

static int command_access(
    uint16_t conn_handle,
    uint16_t attr_handle,
    struct ble_gatt_access_ctxt *ctxt,
    void *arg)
{
    uint16_t length = OS_MBUF_PKTLEN(ctxt->om);
    uint8_t frame[512];
    if (length > sizeof(frame)) return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    if (ble_hs_mbuf_to_flat(ctxt->om, frame, sizeof(frame), NULL) != 0)
        return BLE_ATT_ERR_UNLIKELY;
    ble_protocol_receive_frame(frame, length); // 重组后投递队列，不在回调里跑业务
    return 0;
}

static const struct ble_gatt_svc_def gatt_services[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &SERVICE_UUID.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid = &COMMAND_UUID.u,
                .access_cb = command_access,
                .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            {
                .uuid = &EVENT_UUID.u,
                .val_handle = &s_event_value_handle,
                .flags = BLE_GATT_CHR_F_NOTIFY,
            },
            {0}
        },
    },
    {0}
};
```

上面的 Service 数组只是结构示例。Command/Event 特征建议要求加密连接，但 HMAC 应用认证仍必须保留。

## 5. NimBLE 安全设置

```c
void ble_stack_init(void)
{
    ESP_ERROR_CHECK(nimble_port_init());

    ble_hs_cfg.sync_cb = ble_on_sync;
    ble_hs_cfg.reset_cb = ble_on_reset;
    ble_hs_cfg.store_status_cb = ble_store_util_status_rr;

    ble_hs_cfg.sm_io_cap = BLE_HS_IO_NO_INPUT_OUTPUT;
    ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_sc = 1;
    ble_hs_cfg.sm_mitm = 0;  // 无屏设备无法数字确认；由 HMAC 补充身份认证

    ble_store_config_init();
    ble_gatts_count_cfg(gatt_services);
    ble_gatts_add_svcs(gatt_services);
    nimble_port_freertos_init(ble_host_task);
}
```

`ble_on_sync()` 中推送 Service UUID 广播，并通过 FreeRTOS EventGroup 等待树莓派连接、订阅 Event 和发送 challenge。

## 6. HMAC

```c
#include "mbedtls/md.h"
#include "mbedtls/sha256.h"

bool make_ble_proof(const char *device_secret,
                    const char *nonce,
                    char proof_hex[65])
{
    uint8_t key[32];
    uint8_t digest[32];
    char message[64];

    if (mbedtls_sha256((const unsigned char *)device_secret,
                       strlen(device_secret), key, 0) != 0)
        return false;

    int n = snprintf(message, sizeof(message), "bbdwz-ble-v1:%s", nonce);
    if (n <= 0 || n >= sizeof(message)) return false;

    const mbedtls_md_info_t *info =
        mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    if (!info || mbedtls_md_hmac(info, key, sizeof(key),
            (const unsigned char *)message, strlen(message), digest) != 0)
        return false;

    random_bytes_to_hex(digest, sizeof(digest), proof_hex, 65);
    return true;
}
```

不要打印 key、proof 或 device_secret。

## 7. 分帧结构

```c
typedef struct __attribute__((packed)) {
    uint8_t magic[2];
    uint8_t version;
    uint8_t flags;
    uint32_t message_id;
    uint16_t chunk_index;
    uint16_t chunk_count;
    uint32_t crc32;
} ble_frame_header_t;

_Static_assert(sizeof(ble_frame_header_t) == 16, "BLE header must be 16 bytes");
```

发送 Event：

```c
bool ble_send_payload(uint8_t flags, uint32_t message_id,
                      const uint8_t *payload, size_t payload_len)
{
    size_t chunk_payload = negotiated_mtu - 3 - sizeof(ble_frame_header_t);
    uint16_t count = (payload_len + chunk_payload - 1) / chunk_payload;
    uint32_t crc = protocol_crc32(payload, payload_len);

    for (uint16_t i = 0; i < count; ++i) {
        size_t offset = i * chunk_payload;
        size_t length = MIN(chunk_payload, payload_len - offset);
        ble_frame_header_t header = {
            .magic = {'B','D'}, .version = 1, .flags = flags,
            .message_id = message_id, .chunk_index = i,
            .chunk_count = count, .crc32 = crc,
        };
        struct os_mbuf *om = ble_hs_mbuf_from_flat(&header, sizeof(header));
        if (!om) return false;
        os_mbuf_append(om, payload + offset, length);
        if (ble_gatts_notify_custom(s_connection_handle,
                                    s_event_value_handle, om) != 0)
            return false;
        /* 等待 mbuf/控制器队列空间，不能无节制循环塞 512 KiB。 */
    }
    return true;
}
```

CRC 必须与服务器的标准 zlib CRC32 对齐，可直接使用下面的实现：

```c
uint32_t protocol_crc32(const uint8_t *data, size_t length)
{
    uint32_t crc = 0xffffffffU;
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xedb88320U & (uint32_t)-(int32_t)(crc & 1));
    }
    return crc ^ 0xffffffffU;
}
```

## 8. 一次 BLE 会话顺序

```text
广播 Service UUID
树莓派连接并订阅 Event
Pi -> challenge
ESP -> auth
Pi -> auth_ok + config
ESP -> heartbeat(active cycle version)
Pi -> result + latest config
ESP 保存较新配置，供下次唤醒
ESP -> status(active cycle version)
ESP -> telemetry（如果需要）
ESP -> photo_begin（如果需要）
ESP -> binary JPEG（ready 后）
ESP -> done
Pi -> complete
断开 BLE
```

每一步用不同 `request_id`；设备只在对应 `result.ok=true` 后清除待上传数据。照片最终确认前不能释放 JPEG buffer 或清除 pending photo。

## 9. Wi-Fi 回退

以下错误均回退 Wi-Fi：

```text
广播等待超时
连接断开
OS pairing 或 HMAC 失败
challenge/result 超时
分片或 CRC 失败
ble-photo-disabled
ble-photo-too-large
wifi_required=true
```

回退时继续使用原 `sample_id`、`photo_id` 和完整 payload，因此服务器能跨 BLE/HTTPS 去重。

Wi-Fi 成功后，如果 BLE 模式为 preferred，设备再广播 `pairing_timeout_sec` 秒。此次只建立/修复 BLE pairing，不重复上传已经由 HTTPS 确认的数据。
