# encoding:utf-8
import requests 

# 服务地址
host = "https://api.map.baidu.com"
uri = "/staticimage/v2"
ak = "KUjoGY4YXn9O86A3AXKpSZO3ZfTTAdpU"

params = {
    # 1. 启用高清模式 (scaler=2)
    "scaler":    "2",
    "width":     "512",
    "height":    "256",
    "zoom":      "11",
    "center":    "116.403874,39.914888",
    
    # 2. paths (保持不变)
    "paths":    "116.288891,40.004261;116.487812,40.017524;116.525756,39.967111;116.536105,39.872373|116.442968,39.797022;116.270494,39.851993;116.275093,39.935251;116.383177,39.923743",
    
    # 3. pathStyles: 蓝色 (0x0000FF), 宽度 8, 透明度 0.5 (50%)
    "pathStyles":    "0x0000FF,8,0.5",
    
    "ak":       ak,
}

url = host + uri
response = requests.get(url, params=params)

if response.status_code == 200 and 'image' in response.headers.get("Content-Type", ""):
    file_name = "baidu_hd_transparent_path.png"
    with open(file_name, "wb") as f:
        f.write(response.content)
    print(f"✅ 高清透明折线图生成成功，已保存为 {file_name}")
else:
    print(f"❌ 请求失败，状态码: {response.status_code}")
    # ... (错误处理逻辑)