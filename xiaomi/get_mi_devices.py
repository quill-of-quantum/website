from mijiaAPI import mijiaAPI, mijiaDevice
import json, os

AUTH_PATH = os.path.expanduser("~/.config/mijia-api/mijia-api-auth.json")

with open(AUTH_PATH, "r") as f:
    auth_data = json.load(f)

api = mijiaAPI(auth_data)

# 创建设备对象
device = mijiaDevice(api, dev_name="小米米家电子温湿度计Pro")

# 直接用属性名读取
temp = device.get("temperature")
humi = device.get("relative_humidity")
bat  = device.get("battery_level")

print(f"🌡 温度: {temp} °C")
print(f"💧 湿度: {humi} %")
print(f"🔋 电量: {bat} %")