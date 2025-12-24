#!/usr/bin/env python3
import os
import json
import time
import csv
from datetime import datetime
from mijiaAPI import mijiaAPI, mijiaDevice

# 认证文件路径（从命令行登录后生成）
AUTH_PATH = os.path.expanduser("~/.config/mijia-api/mijia-api-auth.json")

# CSV 文件路径
CSV_PATH = "/home/bbdwz/projects/website/weather/temperature_inside.csv"

def log_temperature():
    try:
        # 初始化 API
        with open(AUTH_PATH, "r") as f:
            auth_data = json.load(f)
        api = mijiaAPI(auth_data)

        # 获取温湿度计对象
        device = mijiaDevice(api, dev_name="小米米家电子温湿度计Pro")

        # 读取数据
        temp = device.get("temperature")
        humi = device.get("relative_humidity")
        bat  = device.get("battery_level")
        now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 写入 CSV
        file_exists = os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["datetime", "temperature", "humidity", "battery"])
            writer.writerow([now, temp, humi, bat])

        print(f"[{now}] 🌡 {temp}°C 💧 {humi}% 🔋 {bat}%")
    except Exception as e:
        print(f"[ERROR] {datetime.now()}: {e}")

if __name__ == "__main__":
    log_temperature()