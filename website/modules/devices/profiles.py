"""Device-type configuration schemas shared by the API and admin UI."""

BASE_FIELDS = [
    {"key":"sample_interval_sec","label":"工作周期","type":"integer","unit":"秒","min":5,"max":86400,"default":300,"section":"schedule"},
    {"key":"wifi_retry_count","label":"Wi-Fi 重试次数","type":"integer","min":0,"max":10,"default":5,"section":"network"},
    {"key":"wifi_retry_backoff_sec","label":"Wi-Fi 失败后休眠","type":"integer","unit":"秒","min":5,"max":3600,"default":60,"section":"network"},
    {"key":"ble_mode","label":"BLE 优先通信","type":"select","options":[{"value":"off","label":"关闭"},{"value":"preferred","label":"优先 BLE，失败回退 Wi-Fi"}],"default":"off","section":"network"},
    {"key":"ble_attempt_timeout_sec","label":"BLE 日常连接等待","type":"integer","unit":"秒","min":1,"max":15,"default":3,"section":"network"},
    {"key":"ble_pairing_timeout_sec","label":"BLE 首次配对等待","type":"integer","unit":"秒","min":3,"max":60,"default":10,"section":"network"},
    {"key":"ble_photo_enabled","label":"允许 BLE 上传照片","type":"boolean","default":True,"section":"network"},
    {"key":"ble_photo_max_bytes","label":"BLE 照片上限","type":"integer","unit":"字节","min":16384,"max":524288,"default":524288,"section":"network"},
]

UPLOAD_FIELD = {"key":"upload_enabled","label":"采集并上传数据","type":"boolean","default":False,"section":"sensor","component":"sensor"}
OV5640_RESOLUTIONS = [
    {"value":"320x240","label":"QVGA 320 × 240"},
    {"value":"640x480","label":"VGA 640 × 480"},
    {"value":"800x600","label":"SVGA 800 × 600"},
    {"value":"1024x768","label":"XGA 1024 × 768"},
    {"value":"1280x720","label":"HD 1280 × 720"},
    {"value":"1600x1200","label":"UXGA 1600 × 1200"},
    {"value":"1920x1080","label":"FHD 1920 × 1080"},
    {"value":"2048x1536","label":"QXGA 2048 × 1536"},
    {"value":"2560x1920","label":"QSXGA 2560 × 1920"},
]

PROFILES = {
    "generic":{"label":"通用设备","fields":BASE_FIELDS+[UPLOAD_FIELD],"telemetry":{"data":"由 capabilities 声明的 JSON 数据"}},
    "temperature":{"label":"温湿度与摄像头设备","schema_revision":3,"fields":BASE_FIELDS+[
        {**UPLOAD_FIELD,"label":"温湿度采集并上传","component":"temperature_humidity"},
        {"key":"temperature_offset_c","label":"温度校准","type":"number","unit":"°C","min":-20,"max":20,"step":0.1,"default":0,"section":"sensor"},
        {"key":"humidity_offset_percent","label":"湿度校准","type":"number","unit":"%","min":-30,"max":30,"step":0.1,"default":0,"section":"sensor"},
        {"key":"photo_enabled","label":"每周期拍照并上传","type":"boolean","default":False,"section":"camera","component":"camera"},
        {"key":"camera_resolution","label":"OV5640 分辨率","type":"select","options":OV5640_RESOLUTIONS,"default":"800x600","section":"camera"},
        {"key":"jpeg_quality","label":"JPEG 质量（数值越小质量越高）","type":"integer","min":5,"max":63,"default":12,"section":"camera"}],
        "telemetry":{"temperature_c":"number","humidity_percent":"number","battery_percent":"number|null"}},
    "camera":{"label":"定时拍照设备","fields":BASE_FIELDS+[
        {"key":"photo_enabled","label":"每周期拍照并上传","type":"boolean","default":False,"section":"camera","component":"camera"},
        {"key":"capture_interval_sec","label":"拍照间隔","type":"integer","unit":"秒","min":60,"max":604800,"default":900},
        {"key":"camera_resolution","label":"OV5640 分辨率","type":"select","options":OV5640_RESOLUTIONS,"default":"800x600","section":"camera"},
        {"key":"jpeg_quality","label":"JPEG 质量（数值越小质量越高）","type":"integer","min":5,"max":63,"default":12,"section":"camera"},
        {"key":"flash_enabled","label":"拍照补光","type":"boolean","default":False}],
        "telemetry":{"photo_id":"string","width":"integer","height":"integer","battery_percent":"number|null"}},
    "environment":{"label":"环境传感器","fields":BASE_FIELDS+[UPLOAD_FIELD,
        {"key":"report_change_threshold","label":"变化上报阈值","type":"number","min":0,"max":100000,"step":0.1,"default":0}],
        "telemetry":{"co2_ppm":"number|null","pressure_pa":"number|null","light_lux":"number|null","voc_ppb":"number|null"}},
    "switch":{"label":"开关/执行器","fields":BASE_FIELDS+[UPLOAD_FIELD,
        {"key":"default_state","label":"上电默认状态","type":"select","options":[{"value":"off","label":"关闭"},{"value":"on","label":"开启"},{"value":"restore","label":"恢复断电前状态"}],"default":"off"},
        {"key":"max_on_sec","label":"单次最长开启","type":"integer","unit":"秒","min":0,"max":86400,"default":0}],
        "telemetry":{"state":"boolean","battery_percent":"number|null"}},
}

ALIASES={"temp_humidity":"temperature","cam":"camera","esp32s3_cam":"camera"}

def profile_for(device_type):
    key=ALIASES.get(str(device_type or "generic").strip().lower(),str(device_type or "generic").strip().lower())
    return key if key in PROFILES else "generic", PROFILES.get(key,PROFILES["generic"])

def schema_for(device_type):
    key,profile=profile_for(device_type)
    return {"id":key,"label":profile["label"],"fields":profile["fields"],"telemetry":profile["telemetry"]}

def schema_revision_for(device_type):
    _,profile=profile_for(device_type)
    return int(profile.get("schema_revision",0))

def effective_config(device_type,saved):
    _,profile=profile_for(device_type); saved=saved if isinstance(saved,dict) else {}
    return {field["key"]:saved.get(field["key"],field.get("default")) for field in profile["fields"]}

def validate_config(device_type,values,current=None):
    _,profile=profile_for(device_type); values=values if isinstance(values,dict) else {}; result=effective_config(device_type,current); errors={}
    for field in profile["fields"]:
        key=field["key"]
        if key not in values: continue
        value=values[key]; kind=field["type"]
        if kind=="boolean":
            if not isinstance(value,bool): errors[key]="必须是布尔值"; continue
        elif kind=="integer":
            if isinstance(value,bool) or not isinstance(value,(int,float)) or int(value)!=value: errors[key]="必须是整数"; continue
            value=int(value)
        elif kind=="number":
            if isinstance(value,bool) or not isinstance(value,(int,float)): errors[key]="必须是数字"; continue
            value=float(value)
        elif kind=="select" and value not in {o["value"] for o in field.get("options",[])}: errors[key]="不是允许的选项"; continue
        if kind in {"integer","number"} and "min" in field and value<field["min"]: errors[key]=f"不能小于 {field['min']}"; continue
        if kind in {"integer","number"} and "max" in field and value>field["max"]: errors[key]=f"不能大于 {field['max']}"; continue
        result[key]=value
    return result,errors
