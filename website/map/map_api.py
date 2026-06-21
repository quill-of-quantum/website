# /home/bbdwz/projects/website/map_api.py
# -*- coding: utf-8 -*-
import os
import json
import requests
import hashlib
import urllib.parse
import math
import folium
import io
import sqlite3
import shutil
import branca.colormap as cm
import time
from flask import Blueprint, request, jsonify, send_file, send_from_directory
from datetime import datetime, timezone

try:
    import srtm
except ImportError:
    print("Warning: srtm module is not installed")

bp = Blueprint('map', __name__, url_prefix='/api/map')

# ========================
# 配置
# ========================
MAP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(MAP_DIR, "config.json")
HISTORY_FILE = os.path.join(MAP_DIR, "history.json")
FAVORITES_FILE = os.path.join(MAP_DIR, "favorites.json")
FAVORITE_IMAGES_DIR = os.path.join(MAP_DIR, "favorite_images")
DB_FILE = os.path.join(MAP_DIR, "geocode_cache.db")  # 👈 新增：数据库文件路径

# 自动初始化 SQLite 数据库
def init_db():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS geocode_cache (
                query_keyword TEXT PRIMARY KEY,
                result_data TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")

init_db() # 模块加载时立刻执行建表

# 你的百度地图 API 凭证
AK = "8xLEtsdbow5oHCPBDWLP5OBgbo61CCst"
SK = "6IASi6Zx1bSRvytGKnHZpxmVpA60FPoN"
# 静态地图专用 AK（用于高清图片生成）
STATIC_MAP_AK = "KUjoGY4YXn9O86A3AXKpSZO3ZfTTAdpU"

os.makedirs(MAP_DIR, exist_ok=True)
os.makedirs(FAVORITE_IMAGES_DIR, exist_ok=True)

# ========================
# 工具函数
# ========================

def load_config():
    """加载配置文件"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {
        "oil_price": 7.0,
        "other": 15.0,
        "oil_consumption": {
            "highway": 6.5,      # 高速路油耗
            "national": 7.5,     # 国道油耗
            "urban": 9.0         # 市区油耗
        }
    }

def save_config(config):
    """保存配置文件"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def load_history():
    """加载历史记录"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {"trips": []}

def save_history(history):
    """保存历史记录"""
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def load_favorites():
    """加载收藏路线"""
    if os.path.exists(FAVORITES_FILE):
        try:
            with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {"favorites": []}

def save_favorites(favorites):
    """保存收藏路线"""
    with open(FAVORITES_FILE, 'w', encoding='utf-8') as f:
        json.dump(favorites, f, ensure_ascii=False, indent=2)

def cache_favorite_map_image(trip_id, map_url):
    """收藏时下载静态地图到本地，后续收藏列表直接使用本地图片。"""
    if not trip_id or not map_url:
        return None, None
    try:
        parsed = urllib.parse.urlparse(map_url)
        if parsed.scheme not in ("http", "https"):
            return None, None
        filename = f"fav_{trip_id}.png"
        image_path = os.path.join(FAVORITE_IMAGES_DIR, filename)
        resp = requests.get(map_url, timeout=10, stream=True)
        content_type = resp.headers.get("Content-Type", "")
        if resp.status_code != 200 or "image" not in content_type:
            return None, None
        tmp_path = image_path + ".tmp"
        total = 0
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > 5 * 1024 * 1024:
                    f.close()
                    os.remove(tmp_path)
                    return None, None
                f.write(chunk)
        os.replace(tmp_path, image_path)
        return filename, f"/api/map/favorite_images/{filename}"
    except Exception as e:
        print(f"❌ 收藏静态图保存失败: {e}")
        return None, None

def delete_favorite_map_image(fav):
    """删除收藏时同步删除本地静态图。"""
    filenames = []
    image_file = fav.get("map_image_file") if isinstance(fav, dict) else None
    if image_file:
        filenames.append(image_file)
    fav_id = fav.get("id") if isinstance(fav, dict) else None
    if fav_id:
        filenames.append(f"fav_{fav_id}.png")
    for filename in dict.fromkeys(filenames):
        safe_name = os.path.basename(filename)
        if safe_name != filename:
            continue
        path = os.path.join(FAVORITE_IMAGES_DIR, safe_name)
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            print(f"❌ 收藏静态图删除失败: {e}")

def attach_existing_favorite_images(favorites):
    """只关联已存在的本地图片，不在读取收藏时联网生成图片。"""
    changed = False
    for fav in favorites.get("favorites", []):
        fav_id = fav.get("id")
        if not fav_id or fav.get("map_image_url"):
            continue
        filename = f"fav_{fav_id}.png"
        if os.path.exists(os.path.join(FAVORITE_IMAGES_DIR, filename)):
            fav["map_image_file"] = filename
            fav["map_image_url"] = f"/api/map/favorite_images/{filename}"
            changed = True
    if changed:
        save_favorites(favorites)
    return favorites

def get_cached_geocode(keyword):
    """从数据库读取地点缓存"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT result_data FROM geocode_cache WHERE query_keyword = ?', (keyword,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return json.loads(row[0])
    except Exception as e:
        print(f"⚠️ 读取地点缓存失败: {e}")
    return None

def save_cached_geocode(keyword, data):
    """将成功的解析结果存入数据库"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        # 使用 REPLACE INTO：如果关键词已存在就覆盖更新，不存在就插入新记录
        cursor.execute('''
            REPLACE INTO geocode_cache (query_keyword, result_data, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (keyword, json.dumps(data, ensure_ascii=False)))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ 写入地点缓存失败: {e}")

def geocode_address(address, search_region="全国", depth=0):
    """
    使用百度地图地点检索 API 将地名转换为经纬度（带 SQLite 缓存）
    """
    # 1. 净化输入关键词
    clean_address = address.strip()
    if not clean_address:
        return None

    # 2. 拦截：如果是初次查询，先查本地数据库缓存
    if depth == 0:
        cached_result = get_cached_geocode(clean_address)
        if cached_result:
            print(f"⚡ 命中本地缓存，免 API 调用: [{clean_address}]")
            return cached_result

    # 3. 缓存未命中，按原逻辑继续调用百度 API
    if depth > 2:
        print(f"❌ 递归层数过深，放弃解析 ({address})")
        return None

    api_path = "/place/v3/region"
    params = {
        "query": clean_address,  # 使用清洗后的词
        "region": search_region,
        "output": "json",
        "ak": AK,
    }
    
    sorted_params = sorted(params.items(), key=lambda x: x[0])
    query_str = api_path + "?" + "&".join([f"{k}={v}" for k, v in sorted_params])
    encoded_str = urllib.parse.quote(query_str, safe="/:=&?#+!$,;'@()*[]")
    raw_str = encoded_str + SK
    sn = hashlib.md5(urllib.parse.quote_plus(raw_str).encode()).hexdigest()
    
    full_url = "https://api.map.baidu.com" + api_path + "?" + "&".join([f"{k}={v}" for k, v in sorted_params]) + f"&sn={sn}"
    
    try:
        resp = requests.get(full_url, timeout=10)
        data = resp.json()
        
        if data.get("status") == 0 and data.get("results"):
            result_type = data.get("result_type", "")

            if result_type == "city_type":
                top_city = data["results"][0].get("name")
                print(f"⚠️ [{clean_address}] 触发城市列表，自动重定向至最热城市: {top_city}")
                return geocode_address(clean_address, search_region=top_city, depth=depth + 1)

            res = data["results"][0]
            location = res.get("location")
            if location:
                final_result = {
                    "coords": f"{location['lat']},{location['lng']}",
                    "name": res.get("name"),
                    "province": res.get("province", ""),
                    "city": res.get("city", ""),
                    "address": res.get("address", "")
                }
                
                # 4. 落地：拿到准确结果后，将其写入本地数据库缓存
                save_cached_geocode(clean_address, final_result)
                print(f"💾 新地点已存入数据库: [{clean_address}]")
                
                return final_result
    except Exception as e:
        print(f"❌ 地理编码失败 ({clean_address}): {e}")
    
    return None

@bp.route('/reverse_geocode', methods=['GET'])
def api_reverse_geocode():
    """逆地理编码接口，用于将经纬度转为地名"""
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    if not lat or not lon:
        return jsonify({"error": "Missing coordinates"}), 400
    
    api_path = "/reverse_geocoding/v3/"
    params = {
        "ak": AK,
        "output": "json",
        "coordtype": "wgs84ll",
        "location": f"{lat},{lon}"
    }
    
    sorted_params = sorted(params.items(), key=lambda x: x[0])
    query_str = api_path + "?" + "&".join([f"{k}={v}" for k, v in sorted_params])
    encoded_str = urllib.parse.quote(query_str, safe="/:=&?#+!$,;'@()*[]")
    raw_str = encoded_str + SK
    sn = hashlib.md5(urllib.parse.quote_plus(raw_str).encode()).hexdigest()
    
    full_url = "https://api.map.baidu.com" + api_path + "?" + "&".join([f"{k}={v}" for k, v in sorted_params]) + f"&sn={sn}"
    
    try:
        resp = requests.get(full_url, timeout=10)
        data = resp.json()
        if data.get("status") == 0:
            address = data["result"].get("formatted_address")
            return jsonify({"address": address})
    except Exception as e:
        print(f"❌ 逆地理编码失败: {e}")
    
    return jsonify({"error": "Reverse geocoding failed"}), 500

def calculate_route(origin, destination, waypoints=None):
    """
    调用百度地图路线规划 API
    返回: 路线数据或 None
    """
    api_path = "/directionlite/v1/driving"
    timestamp = str(int(datetime.now().timestamp() * 1000))
    
    params = {
        "ak": AK,
        "destination": destination,
        "origin": origin,
        "tactics": "0",
        "timestamp": timestamp
    }
    
    if waypoints:
        params["waypoints"] = "|".join(waypoints)
    
    sorted_params = sorted(params.items(), key=lambda x: x[0])
    query_str = api_path + "?" + "&".join([f"{k}={v}" for k, v in sorted_params])
    
    encoded_str = urllib.parse.quote(query_str, safe="/:=&?#+!$,;'@()*[]")
    raw_str = encoded_str + SK
    quoted_plus_str = urllib.parse.quote_plus(raw_str)
    sn = hashlib.md5(quoted_plus_str.encode()).hexdigest()
    
    url = "https://api.map.baidu.com" + api_path
    full_url = url + "?" + "&".join([f"{k}={v}" for k, v in sorted_params]) + f"&sn={sn}"
    
    try:
        resp = requests.get(full_url, timeout=10)
        data = resp.json()
        
        if data.get("status") == 0:
            return data["result"]["routes"][0] if data["result"]["routes"] else None
    except Exception as e:
        print(f"❌ 路线规划失败: {e}")
    
    return None

def generate_static_map(origin, destination, waypoints=None):
    """
    生成百度地图静态图片URL（使用高清模式）
    返回: 地图图片URL或 None
    """
    host = "https://api.map.baidu.com"
    uri = "/staticimage/v2"
    
    # origin 和 destination 的格式是 "lat,lng"，需要反转为 "lng,lat"
    origin_parts = origin.split(',')
    dest_parts = destination.split(',')
    
    origin_coord = f"{origin_parts[1]},{origin_parts[0]}"  # lng,lat
    dest_coord = f"{dest_parts[1]},{dest_parts[0]}"  # lng,lat
    
    # 构建paths参数：格式为 起点 → 途经点… → 终点
    paths_list = [origin_coord]
    if waypoints:
        for wp in waypoints:
            wp_parts = wp.split(',')
            paths_list.append(f"{wp_parts[1]},{wp_parts[0]}")  # lng,lat
    paths_list.append(dest_coord)
    
    paths_str = ";".join(paths_list)
    
    params = {
        "scaler": "2",              # 启用高清模式
        "width": "560",
        "height": "400",
        "paths": paths_str,
        "pathStyles": "0x7C3AED,8,0.75",  # 蓝色、宽度8、透明度75%
        "ak": STATIC_MAP_AK,
    }
    
    url = host + uri
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        
        if resp.status_code == 200 and 'image' in resp.headers.get("Content-Type", ""):
            # 构建完整URL用于返回（可直接在img src中使用）
            full_url = url + "?" + "&".join([f"{k}={v}" for k, v in params.items()])
            print(f"🗺️ 生成地图URL: {full_url}")
            return full_url
        else:
            print(f"❌ 生成静态地图失败: 状态码 {resp.status_code}")
    except Exception as e:
        print(f"❌ 生成静态地图失败: {e}")
    
    return None

def calculate_route_cost(route_data, config):
    """
    根据路段类型计算成本
    返回: (distance_km, duration_min, toll, oil_cost, other_cost, total_cost, breakdown, electric_cost, electric_total_cost)
    """
    distance_km = route_data.get("distance", 0) / 1000
    duration_min = route_data.get("duration", 0) / 60
    toll = route_data.get("toll", 0)
    
    # 按路段类型计算油费
    oil_consumption = config.get("oil_consumption", {})
    if isinstance(oil_consumption, dict):
        highway_consumption = oil_consumption.get("highway", 6.5)
        national_consumption = oil_consumption.get("national", 7.5)
        urban_consumption = oil_consumption.get("urban", 9.0)
    else:
        highway_consumption = national_consumption = urban_consumption = oil_consumption
    
    oil_price = config.get("oil_price", 7.0)
    
    # 按路段类型计算电耗
    electric_consumption = config.get("electric_consumption", {})
    if isinstance(electric_consumption, dict):
        electric_highway = electric_consumption.get("highway", 16.0)
        electric_national = electric_consumption.get("national", 18.0)
        electric_urban = electric_consumption.get("urban", 22.0)
    else:
        electric_highway = electric_national = electric_urban = 16.0
    
    electric_price = config.get("electric_price", 1.5)
    
    # 计算分段油费和电费
    steps = route_data.get("steps", [])
    oil_cost = 0.0
    electric_cost = 0.0
    breakdown = {
        "highway": {"distance": 0, "cost": 0},
        "national": {"distance": 0, "cost": 0},
        "urban": {"distance": 0, "cost": 0}
    }
    
    if steps:
        for step in steps:
            step_distance_km = step.get("distance", 0) / 1000
            road_type = int(step.get("road_type", 6))
            
            # 根据road_type分类
            if road_type in [0, 1]:  # 高速路、城市高速
                oil_segment_cost = (step_distance_km / 100) * highway_consumption * oil_price
                electric_segment_cost = (step_distance_km / 100) * electric_highway * electric_price
                breakdown["highway"]["distance"] += step_distance_km
                breakdown["highway"]["cost"] += oil_segment_cost
                oil_cost += oil_segment_cost
                electric_cost += electric_segment_cost
            elif road_type in [2, 3]:  # 国道、省道
                oil_segment_cost = (step_distance_km / 100) * national_consumption * oil_price
                electric_segment_cost = (step_distance_km / 100) * electric_national * electric_price
                breakdown["national"]["distance"] += step_distance_km
                breakdown["national"]["cost"] += oil_segment_cost
                oil_cost += oil_segment_cost
                electric_cost += electric_segment_cost
            else:  # 其他（市区、县道等）
                oil_segment_cost = (step_distance_km / 100) * urban_consumption * oil_price
                electric_segment_cost = (step_distance_km / 100) * electric_urban * electric_price
                breakdown["urban"]["distance"] += step_distance_km
                breakdown["urban"]["cost"] += oil_segment_cost
                oil_cost += oil_segment_cost
                electric_cost += electric_segment_cost
    else:
        # 如果没有steps数据，使用平均油耗和电耗计算
        oil_cost = (distance_km / 100) * urban_consumption * oil_price
        electric_cost = (distance_km / 100) * electric_urban * electric_price
        breakdown["urban"]["distance"] = distance_km
        breakdown["urban"]["cost"] = oil_cost
    
    other_cost = config.get("other", 15.0)
    total_cost = oil_cost + toll + other_cost
    electric_total_cost = electric_cost + toll + other_cost
    
    return distance_km, duration_min, toll, round(oil_cost, 2), round(other_cost, 2), round(total_cost, 2), breakdown, round(electric_cost, 2), round(electric_total_cost, 2)

def analyze_route_types(route_data):
    """
    分析路段类型分布
    返回: {type_name: {distance_km, count, percentage}, ...}
    """
    road_type_names = {
        0: "高速路",
        1: "城市高速",
        2: "国道",
        3: "省道",
        4: "县道",
        5: "乡道",
        6: "其他",
    }
    
    steps = route_data.get("steps", [])
    type_stats = {}
    total_distance = 0
    
    # 初始化统计
    for i in range(7):
        type_stats[i] = {"distance": 0, "count": 0, "name": road_type_names.get(i, "其他")}
    
    # 统计数据
    for step in steps:
        road_type = int(step.get("road_type", 6))
        distance = step.get("distance", 0)
        
        if road_type not in type_stats:
            road_type = 6
        
        type_stats[road_type]["distance"] += distance
        type_stats[road_type]["count"] += 1
        total_distance += distance
    
    # 计算百分比
    result = {}
    for road_type, stats in type_stats.items():
        if stats["distance"] > 0:
            result[road_type] = {
                "name": stats["name"],
                "distance_km": round(stats["distance"] / 1000, 2),
                "count": stats["count"],
                "percentage": round((stats["distance"] / total_distance * 100), 2)
            }
    
    return result

# ========================
# 坐标转换函数
# ========================

def bd09_to_gcj02(bd_lat, bd_lng):
    """
    将百度坐标 (BD09) 转换为 GCJ02 (高德/腾讯) 坐标
    """
    x_pi = 3.14159265358979324 * 3000.0 / 180.0
    z = math.sqrt(bd_lng * bd_lng + bd_lat * bd_lat) + 0.00002 * math.sin(bd_lat * x_pi)
    theta = math.atan2(bd_lat, bd_lng) + 0.000003 * math.cos(bd_lng * x_pi)
    gcj_lng = z * math.cos(theta) - 0.0065
    gcj_lat = z * math.sin(theta) - 0.006
    return gcj_lat, gcj_lng

def bd09_to_wgs84(bd_lat, bd_lng):
    """
    将百度坐标 (BD09) 转换为 WGS84 坐标 (两步走)
    """
    gcj_lat, gcj_lng = bd09_to_gcj02(bd_lat, bd_lng)
    
    # gcj02 to wgs84
    ee = 0.00669342162296594323
    a = 6378245.0
    pi = 3.1415926535897932384626
    
    def transformlat(lng, lat):
        ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(math.fabs(lng))
        ret += (20.0 * math.sin(6.0 * lng * pi) + 20.0 * math.sin(2.0 * lng * pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(lat * pi) + 40.0 * math.sin(lat / 3.0 * pi)) * 2.0 / 3.0
        ret += (160.0 * math.sin(lat / 12.0 * pi) + 320 * math.sin(lat * pi / 30.0)) * 2.0 / 3.0
        return ret

    def transformlng(lng, lat):
        ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(math.fabs(lng))
        ret += (20.0 * math.sin(6.0 * lng * pi) + 20.0 * math.sin(2.0 * lng * pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(lng * pi) + 40.0 * math.sin(lng / 3.0 * pi)) * 2.0 / 3.0
        ret += (150.0 * math.sin(lng / 12.0 * pi) + 300.0 * math.sin(lng / 30.0 * pi)) * 2.0 / 3.0
        return ret
        
    dlat = transformlat(gcj_lng - 105.0, gcj_lat - 35.0)
    dlng = transformlng(gcj_lng - 105.0, gcj_lat - 35.0)
    radlat = gcj_lat / 180.0 * pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi)
    dlng = (dlng * 180.0) / (a / sqrtmagic * math.cos(radlat) * pi)
    mglat = gcj_lat + dlat
    mglng = gcj_lng + dlng
    
    return gcj_lat * 2 - mglat, gcj_lng * 2 - mglng

def haversine_distance(lon1, lat1, lon2, lat2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2) * math.sin(dlam/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

# === WGS84 to GCJ02 (用于从WGS84插值点还原到高德底图绘制) ===
def wgs84_to_gcj02(lng, lat):
    ee = 0.00669342162296594323
    a = 6378245.0
    pi = 3.1415926535897932384626
    def transformlat(lng, lat):
        ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(math.fabs(lng))
        ret += (20.0 * math.sin(6.0 * lng * pi) + 20.0 * math.sin(2.0 * lng * pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(lat * pi) + 40.0 * math.sin(lat / 3.0 * pi)) * 2.0 / 3.0
        ret += (160.0 * math.sin(lat / 12.0 * pi) + 320 * math.sin(lat * pi / 30.0)) * 2.0 / 3.0
        return ret
    def transformlng(lng, lat):
        ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(math.fabs(lng))
        ret += (20.0 * math.sin(6.0 * lng * pi) + 20.0 * math.sin(2.0 * lng * pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(lng * pi) + 40.0 * math.sin(lng / 3.0 * pi)) * 2.0 / 3.0
        ret += (150.0 * math.sin(lng / 12.0 * pi) + 300.0 * math.sin(lng / 30.0 * pi)) * 2.0 / 3.0
        return ret
    
    dlat = transformlat(lng - 105.0, lat - 35.0)
    dlng = transformlng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi)
    dlng = (dlng * 180.0) / (a / sqrtmagic * math.cos(radlat) * pi)
    mglat = lat + dlat
    mglng = lng + dlng
    return mglat, mglng

def interpolate_line(points, interval=50):
    if not points: return []
    sampled = [points[0]]
    for i in range(1, len(points)):
        p1, p2 = points[i-1], points[i]
        dist = haversine_distance(p1[0], p1[1], p2[0], p2[1])
        if dist > interval:
            steps = int(dist // interval)
            for j in range(1, steps + 1):
                fraction = j / (steps + 1)
                new_lon = p1[0] + (p2[0] - p1[0]) * fraction
                new_lat = p1[1] + (p2[1] - p1[1]) * fraction
                sampled.append((new_lon, new_lat))
        sampled.append(p2)
    return sampled

import colorsys

def check_and_clean_srtm_cache(cache_dir, limit_gb=10):
    """检查 SRTM 缓存大小，如果超过限制则清理"""
    if not os.path.exists(cache_dir):
        return 0
    
    total_size = 0
    for dirpath, _, filenames in os.walk(cache_dir):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if not os.path.islink(fp):
                total_size += os.path.getsize(fp)
                
    if total_size > limit_gb * 1024 * 1024 * 1024:
        print(f"⚠️ SRTM 缓存超过 {limit_gb}GB，执行清空...")
        shutil.rmtree(cache_dir)
        os.makedirs(cache_dir)
        return 0
    
    return total_size

def get_elevation_color(elevation, min_ele, max_ele):
    """根据海拔返回彩虹颜色分布 (蓝 -> 绿 -> 黄 -> 红)"""
    if max_ele == min_ele:
        return '#00FF00'
    ratio = (elevation - min_ele) / (max_ele - min_ele)
    # hue从0.66(蓝色240度) 变化到 0.0(红色0度)
    h = (1.0 - ratio) * 0.666
    r, g, b = colorsys.hsv_to_rgb(h, 1.0, 1.0)
    return f'#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}'

def generate_folium_map(route_data, origin_info, destination_info, waypoints_info=None, waypoint_addresses=None, elevation_data_list=None):
    """
    使用folium生成交互式地图HTML
    返回: HTML字符串
    """
    if isinstance(origin_info, str):
        origin_coords = origin_info
        origin_name = "起点"
    else:
        origin_coords = origin_info.get("coords")
        origin_name = origin_info.get("name", "起点")

    if isinstance(destination_info, str):
        dest_coords = destination_info
        dest_name = "终点"
    else:
        dest_coords = destination_info.get("coords")
        dest_name = destination_info.get("name", "终点")

    # 解析坐标 (格式: lat,lng)
    origin_parts = origin_coords.split(',')
    dest_parts = dest_coords.split(',')
    origin_lat, origin_lng = float(origin_parts[0]), float(origin_parts[1])
    dest_lat, dest_lng = float(dest_parts[0]), float(dest_parts[1])
    
    # 修改为使用 GCJ02 (适配高德底图)，解决不重合和不显示起点的问题
    origin_lat, origin_lng = bd09_to_gcj02(origin_lat, origin_lng)
    dest_lat, dest_lng = bd09_to_gcj02(dest_lat, dest_lng)
    
    # 计算地图中心
    center_lat = (origin_lat + dest_lat) / 2
    center_lng = (origin_lng + dest_lng) / 2
    
    # 创建地图（先用默认缩放）
    m = folium.Map(
        location=[center_lat, center_lng],
        zoom_start=10,
        control_scale=True,
        tiles='https://wprd01.is.autonavi.com/appmaptile?x={x}&y={y}&z={z}&style=7',
        attr='高德地图'
    )
    
    # 路段类型配置
    road_type_colors = {
        0: '#667eea',  # 高速路
        1: '#764ba2',  # 城市高速
        2: '#8B4513',  # 国道
        3: '#43e97b',  # 省道
        4: '#f093fb',  # 县道
        5: '#fa709a',  # 乡道
        6: '#fee140'   # 其他
    }
    
    road_type_names = {
        0: '高速路',
        1: '城市高速',
        2: '国道',
        3: '省道',
        4: '县道',
        5: '乡道',
        6: '其他'
    }
    
    # 绘制各路段
    steps = route_data.get('steps', [])
    valid_segments = 0
    
    # 原始地图路段（根据 road_type 画不同颜色）
    for idx, step in enumerate(steps):
        try:
            distance_km = step.get('distance', 0) / 1000
            duration_min = step.get('duration', 0) / 60
            road_type = int(step.get('road_type', 6))
            instruction = step.get('instruction', '').replace('<b>', '').replace('</b>', '')
            
            polyline = step.get('path', '')
            if not polyline:
                continue
            
            try:
                coords = []
                for point_str in polyline.split(';'):
                    if point_str.strip():
                        lng, lat = point_str.split(',')
                        # 底图使用GCJ02渲染，解决偏移
                        gcj_lat, gcj_lng = bd09_to_gcj02(float(lat), float(lng))
                        coords.append([gcj_lat, gcj_lng])
                
                if len(coords) < 2:
                    continue
                
                color = road_type_colors.get(road_type, '#666666')
                road_name = road_type_names.get(road_type, '其他')
                
                # 添加路段线条
                folium.PolyLine(
                    coords,
                    color=color,
                    weight=5,
                    opacity=0.8,
                    popup=f"<b>第 {idx + 1} 段 - {road_name}</b><br/>{distance_km:.2f} km | {duration_min:.0f} 分钟<br/>{instruction[:50]}",
                    tooltip=f"{road_name}: {distance_km:.2f} km"
                ).add_to(m)
                
                valid_segments += 1
                
            except ValueError as e:
                print(f"⚠️ 路段 {idx + 1} 坐标转换失败: {e}")
                continue
        
        except Exception as e:
            print(f"⚠️ 路段 {idx + 1} 处理异常: {e}")
            continue

    ele_var_name = None  # 👈 新增：先初始化变量
    # 添加一个可选的海拔图层 (彩虹色阶)
    if elevation_data_list and len(elevation_data_list) > 1:
        # 改为默认显示 (show=True)
        ele_group = folium.FeatureGroup(name='🌈 海拔彩虹路线 (紫低红高)', show=True)
        
        # 为了防止路线过长导致浏览器卡顿，这里保留抽稀逻辑，ColorLine 性能较好，放宽到 500 个点
        step_sz = max(1, len(elevation_data_list) // 5000)
        chart_elevation_list = elevation_data_list[::step_sz]
        
        # 提取坐标点和对应的海拔高度
        coords = [[pt['gcj_lat'], pt['gcj_lng']] for pt in chart_elevation_list]
        elevations = [pt['ele'] for pt in chart_elevation_list]
        
        min_ele = min(elevations)
        max_ele = max(elevations)
        
        # 1. 对齐前端的 Y 轴极值算法 (向上下取百位整数)
        import math
        chart_min_ele = math.floor(min_ele / 100.0) * 100
        chart_max_ele = math.ceil(max_ele / 100.0) * 100
        
        if chart_min_ele == chart_max_ele:
            chart_max_ele += 100
            
        ele_range = chart_max_ele - chart_min_ele
        
        # 2. 精确匹配前端 Canvas 渐变停靠点 (从低到高排列)
        # 前端从上(红)到下(紫)比例为: 0, 0.15, 0.3, 0.5, 0.65, 0.8, 1
        color_index = [
            chart_min_ele,                               # 紫 (最低点)
            chart_min_ele + ele_range * (1 - 0.80),      # 蓝
            chart_min_ele + ele_range * (1 - 0.65),      # 青
            chart_min_ele + ele_range * (1 - 0.50),      # 绿
            chart_min_ele + ele_range * (1 - 0.30),      # 黄
            chart_min_ele + ele_range * (1 - 0.15),      # 橙
            chart_max_ele                                # 红 (最高点)
        ]
        
        # 生成完全对齐的色带
        colormap = cm.LinearColormap(
            colors=['#8b00ff', '#0000ff', '#00ffff', '#00ff00', '#ffff00', '#ff7f00', '#ff0000'],
            index=color_index,
            vmin=chart_min_ele,
            vmax=chart_max_ele
        )
        
        # 使用 ColorLine 自动根据海拔上色
        folium.ColorLine(
            positions=coords,
            colors=elevations,
            colormap=colormap,
            weight=7,       # 加粗一点覆盖在原路线上更清晰
            opacity=0.9
        ).add_to(ele_group)
        
        # 将彩色图例添加到地图右下角
        colormap.caption = '海拔高度 (米)'
        colormap.add_to(m)
        
        ele_group.add_to(m)
        # 添加图层控制，允许用户在右上角自由切换路段/海拔图层
        ele_var_name = ele_group.get_name()
        
    # 添加起点标记
    try:
        folium.Marker(
            location=[origin_lat, origin_lng],
            popup=f'<b>起点: {origin_name}</b>',
            tooltip=origin_name,
            icon=folium.Icon(color='green', icon='play', prefix='fa')
        ).add_to(m)
    except Exception as e:
        print(f"⚠️ 起点标记添加失败: {e}")
    
    # 添加途径点标记
    if waypoints_info:
        for idx, wp in enumerate(waypoints_info):
            try:
                if isinstance(wp, str):
                    wp_coords = wp
                    wp_name = waypoint_addresses[idx] if waypoint_addresses and idx < len(waypoint_addresses) else f'途径点 {idx + 1}'
                else:
                    wp_coords = wp.get("coords")
                    wp_name = wp.get("name", f'途径点 {idx + 1}')

                wp_parts = wp_coords.split(',')
                wp_lat, wp_lng = float(wp_parts[0]), float(wp_parts[1])
                wp_lat, wp_lng = bd09_to_gcj02(wp_lat, wp_lng)
                
                folium.Marker(
                    location=[wp_lat, wp_lng],
                    popup=f'<b>途径点 {idx + 1}: {wp_name}</b>',
                    tooltip=wp_name,
                    icon=folium.Icon(color='blue', icon='map-pin', prefix='fa')
                ).add_to(m)
            except Exception as e:
                print(f"⚠️ 途径点 {idx + 1} 标记添加失败: {e}")
    
    # 添加终点标记
    try:
        folium.Marker(
            location=[dest_lat, dest_lng],
            popup=f'<b>终点: {dest_name}</b>',
            tooltip=dest_name,
            icon=folium.Icon(color='red', icon='stop', prefix='fa')
        ).add_to(m)
    except Exception as e:
        print(f"⚠️ 终点标记添加失败: {e}")
    
    # 计算所有坐标点的边界（用于自动缩放）
    all_lats = [origin_lat, dest_lat]
    all_lngs = [origin_lng, dest_lng]
    
    # 添加途径点坐标到边界计算
    if waypoints_info:
        for wp in waypoints_info:
            try:
                if isinstance(wp, str):
                    wp_coords = wp
                else:
                    wp_coords = wp.get("coords")
                    
                wp_parts = wp_coords.split(',')
                wp_lat, wp_lng = float(wp_parts[0]), float(wp_parts[1])
                wp_lat, wp_lng = bd09_to_gcj02(wp_lat, wp_lng)
                all_lats.append(wp_lat)
                all_lngs.append(wp_lng)
            except:
                pass
    
    # 添加路段上所有的点到边界计算
    steps = route_data.get('steps', [])
    for step in steps:
        try:
            polyline = step.get('path', '')
            if polyline:
                for point_str in polyline.split(';'):
                    if point_str.strip():
                        lng, lat = point_str.split(',')
                        gcj_lat, gcj_lng = bd09_to_gcj02(float(lat), float(lng))
                        all_lats.append(gcj_lat)
                        all_lngs.append(gcj_lng)
        except:
            pass
    
    # 自动调整地图边界以适应所有坐标
    if all_lats and all_lngs:
        min_lat, max_lat = min(all_lats), max(all_lats)
        min_lng, max_lng = min(all_lngs), max(all_lngs)
        
        # 添加10%的边距
        lat_margin = (max_lat - min_lat) * 0.1
        lng_margin = (max_lng - min_lng) * 0.1
        
        # 使用 fit_bounds 自动缩放到合适的视图
        m.fit_bounds(
            [[min_lat - lat_margin, min_lng - lng_margin],
             [max_lat + lat_margin, max_lng + lng_margin]]
        )
    
    # 添加图例（默认隐藏）
    legend_html = '''
    <div id="mapLegend" style="position: fixed; 
                bottom: 50px; right: 50px; width: 200px; height: auto;
                background-color: white; border:2px solid grey; z-index:9999; 
                font-size:12px; padding: 10px; border-radius: 5px;
                box-shadow: 0 0 15px rgba(0,0,0,0.2); display: none;">
    <p style="margin: 0 0 10px 0; font-weight: bold;">🛣️ 路段类型</p>
    '''
    
    for road_type in sorted(road_type_colors.keys()):
        color = road_type_colors[road_type]
        name = road_type_names.get(road_type, '其他')
        legend_html += f'<p style="margin: 5px 0;"><i style="background:{color}; width: 15px; height: 2px; display: inline-block; margin-right: 5px;"></i>{name}</p>'
    
    legend_html += '</div>'
    
    m.get_root().html.add_child(folium.Element(legend_html))
    
# === 提前计算好 WGS84 的区域边界，注入给前端备用 ===
    topo_bounds_json = "null"
    if elevation_data_list and len(elevation_data_list) > 1:
        wgs_lats = [pt['lat'] for pt in chart_elevation_list]
        wgs_lngs = [pt['lng'] for pt in chart_elevation_list]
        min_wlat, max_wlat = min(wgs_lats), max(wgs_lats)
        min_wlng, max_wlng = min(wgs_lngs), max(wgs_lngs)
        lat_margin = max((max_wlat - min_wlat) * 0.5, 0.02)
        lng_margin = max((max_wlng - min_wlng) * 0.5, 0.02)
        
        import json
        topo_bounds_json = json.dumps({
            "min_lat": min_wlat - lat_margin,
            "max_lat": max_wlat + lat_margin,
            "min_lng": min_wlng - lng_margin,
            "max_lng": max_wlng + lng_margin
        })

    # 获取底图的 JS 变量名
    map_var_name = m.get_name()
    
    ele_btn_html = ""
    topo_btn_html = ""
    if ele_var_name:
        ele_btn_html = f'''
        <button id="toggleEleBtn" onclick="toggleElevationLayer()" style="
            padding: 8px 12px; background-color: #e53e3e; color: white; border: none;
            border-radius: 5px; cursor: pointer; font-weight: 600; font-size: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2); transition: all 0.3s ease;
        " onmouseover="this.style.transform='scale(1.05)'" onmouseout="this.style.transform='scale(1)'">
            🌈 海拔线
        </button>
        '''
        topo_btn_html = f'''
        <button id="toggleTopoBtn" onclick="toggleTopoLayer()" style="
            padding: 8px 12px; background-color: #a0aec0; color: white; border: none;
            border-radius: 5px; cursor: pointer; font-weight: 600; font-size: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2); transition: all 0.3s ease;
        " onmouseover="this.style.transform='scale(1.05)'" onmouseout="this.style.transform='scale(1)'">
            🌍 区域地形
        </button>
        '''

    ele_js = f'''
    setTimeout(function() {{
        var myMap = {map_var_name};
        var eleBtn = document.getElementById('toggleEleBtn');
        var topoBtn = document.getElementById('toggleTopoBtn');
        var colormapContainer = null;
        var topoLayer = null; // 存放前端渲染的地形图层
        var grid_sz = 500;    // 👈 你可以在这里方便地调整请求的矩阵尺寸

        function pushTopoPerfLog(label, time, type, unit) {{
            var log = {{
                label: label,
                time: time,
                type: type || 'main',
                unit: unit
            }};
            if (window.parent && typeof window.parent.addPerfLog === 'function') {{
                window.parent.addPerfLog(log);
            }} else if (typeof perfLogs !== 'undefined' && typeof renderPerfLogs === 'function') {{
                perfLogs.push(log);
                renderPerfLogs();
            }}
        }}

        function estimateTopoMetrics(bounds, gridSize) {{
            var latSpan = Math.abs(bounds.max_lat - bounds.min_lat);
            var lngSpan = Math.abs(bounds.max_lng - bounds.min_lng);
            var midLatRad = ((bounds.max_lat + bounds.min_lat) / 2) * Math.PI / 180;
            var heightKm = latSpan * 111.32;
            var widthKm = lngSpan * 111.32 * Math.cos(midLatRad);
            var areaKm2 = Math.max(0, widthKm * heightKm);
            var cellWidthM = gridSize > 1 ? (widthKm * 1000) / (gridSize - 1) : 0;
            var cellHeightM = gridSize > 1 ? (heightKm * 1000) / (gridSize - 1) : 0;
            return {{
                widthKm: widthKm,
                heightKm: heightKm,
                areaKm2: areaKm2,
                cellWidthM: cellWidthM,
                cellHeightM: cellHeightM,
                sampleCount: gridSize * gridSize
            }};
        }}

        function setTopoNotice(message) {{
            if (!topoBtn) return;
            var notice = document.getElementById('topoNotice');
            if (!notice) {{
                notice = document.createElement('span');
                notice.id = 'topoNotice';
                notice.style.cssText = 'align-self:center; max-width:220px; padding:6px 8px; background:rgba(255,255,255,0.95); color:#b7791f; border:1px solid #f6e05e; border-radius:5px; font-size:12px; line-height:1.35; box-shadow:0 2px 8px rgba(0,0,0,0.12);';
                topoBtn.parentNode.insertBefore(notice, topoBtn.nextSibling);
            }}
            notice.textContent = message || '';
            notice.style.display = message ? 'inline-block' : 'none';
        }}
        
        // 找图例并自动缩放移动
        var allSvgs = document.querySelectorAll('svg');
        for (var i = 0; i < allSvgs.length; i++) {{
            if (allSvgs[i].textContent.includes('海拔高度')) {{
                colormapContainer = allSvgs[i].closest('.leaflet-control') || allSvgs[i];
                colormapContainer.style.marginTop = '50px';
                colormapContainer.style.transformOrigin = 'top right';
                if (window.innerWidth <= 768) colormapContainer.style.transform = 'scale(0.75)';
                break;
            }}
        }}
        
        // HSL 转 RGB 的工具函数
        function hslToRgb(h, s, l) {{
            var r, g, b;
            if (s == 0) {{ r = g = b = l; }} else {{
                var hue2rgb = function(p, q, t) {{
                    if (t < 0) t += 1;
                    if (t > 1) t -= 1;
                    if (t < 1/6) return p + (q - p) * 6 * t;
                    if (t < 1/2) return q;
                    if (t < 2/3) return p + (q - p) * (2/3 - t) * 6;
                    return p;
                }};
                var q = l < 0.5 ? l * (1 + s) : l + s - l * s;
                var p = 2 * l - q;
                r = hue2rgb(p, q, h + 1/3);
                g = hue2rgb(p, q, h);
                b = hue2rgb(p, q, h - 1/3);
            }}
            return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
        }}
        
        if (eleBtn) {{
            eleBtn.dataset.active = 'true';
            window.toggleElevationLayer = function() {{
                var eleLayer = {ele_var_name if ele_var_name else 'null'};
                if (!eleLayer) return;
                if (eleBtn.dataset.active === 'true') {{
                    myMap.removeLayer(eleLayer);
                    eleBtn.dataset.active = 'false';
                    eleBtn.style.background = '#a0aec0';
                    if (colormapContainer) colormapContainer.style.display = 'none';
                }} else {{
                    myMap.addLayer(eleLayer);
                    eleBtn.dataset.active = 'true';
                    eleBtn.style.background = '#e53e3e';
                    if (colormapContainer) colormapContainer.style.display = 'block';
                }}
            }};
        }}
        
        if (topoBtn) {{
            topoBtn.dataset.active = 'false';
            window.toggleTopoLayer = function() {{
                if (topoBtn.dataset.active === 'true') {{
                    myMap.removeLayer(topoLayer);
                    topoBtn.dataset.active = 'false';
                    topoBtn.style.background = '#a0aec0';
                }} else {{
                    // 如果还未加载，则发起请求
                    if (!topoLayer) {{
                        var bounds = {topo_bounds_json};
                        if (!bounds) return;
                        var topoMetrics = estimateTopoMetrics(bounds, grid_sz);
                        if (topoMetrics.areaKm2 > 250000) {{
                            setTopoNotice('区域面积较大，地形图可能加载较慢或失败，请稍候。');
                        }} else {{
                            setTopoNotice('');
                        }}
                        
                        topoBtn.innerHTML = '⏳ 加载中...';
                        topoBtn.style.background = '#d69e2e';
                        var topoFetchStart = performance.now();
                        
                        fetch('/api/map/topo', {{
                            method: 'POST',
                            headers: {{'Content-Type': 'application/json'}},
                            body: JSON.stringify({{bounds: bounds, grid_size: grid_sz}})
                        }}).then(r => r.text()).then(rawText => {{
                            var topoFetchMs = performance.now() - topoFetchStart;
                            var topoPayloadMB = (new Blob([rawText]).size / (1024 * 1024)).toFixed(2);
                            var data = JSON.parse(rawText);
                            if (data.status !== 'ok') {{
                                throw new Error(data.error || '区域地形加载失败');
                            }}
                            pushTopoPerfLog('🌍 区域地形请求总耗时', topoFetchMs, 'main');
                            pushTopoPerfLog('📦 区域地形响应体积', topoPayloadMB, 'main', 'MB');
                            pushTopoPerfLog('🧩 区域地形采样矩阵', grid_sz + ' x ' + grid_sz + '（' + topoMetrics.sampleCount.toLocaleString() + '点）', 'main', '');
                            pushTopoPerfLog('🗺️ 区域地形覆盖范围', topoMetrics.widthKm.toFixed(1) + ' x ' + topoMetrics.heightKm.toFixed(1) + ' km（约 ' + topoMetrics.areaKm2.toFixed(0) + ' km²）', 'main', '');
                            pushTopoPerfLog('📐 区域地形单格分辨率', topoMetrics.cellWidthM.toFixed(0) + ' x ' + topoMetrics.cellHeightM.toFixed(0) + ' m/格', 'main', '');
                            // 前端创建不可见 Canvas 进行极速像素渲染
                            var canvas = document.createElement('canvas');
                            canvas.width = grid_sz;
                            canvas.height = grid_sz;
                            var ctx = canvas.getContext('2d');
                            var imgData = ctx.createImageData(grid_sz, grid_sz);
                            
                            var min = data.min_ele;
                            var max = data.max_ele;
                            if (min === max) max += 100;
                            
                            // 🚀 核心修复：建立视觉均匀的分段色阶 (红,橙,黄,绿,青,蓝,紫对应的 Hue 值)
                            var stops = [0, 30, 60, 120, 180, 240, 270]; 
                            
                            for (var r = 0; r < grid_sz; r++) {{
                                for (var c = 0; c < grid_sz; c++) {{
                                    var val = data.grid[r][c];
                                    var ratio = (val - min) / (max - min);
                                    if (ratio < 0) ratio = 0; if (ratio > 1) ratio = 1;
                                    
                                    // 强制将数据比例等分成 6 个视觉区间进行插值
                                    var revRatio = 1 - ratio; // 反转，0为最高海拔(红)，1为最低海拔(紫)
                                    var segment = Math.floor(revRatio * 6);
                                    var h = 270 / 360; // 默认兜底为紫色
                                    
                                    if (segment < 6) {{
                                        var fraction = (revRatio * 6) - segment;
                                        h = (stops[segment] + fraction * (stops[segment+1] - stops[segment])) / 360;
                                    }}
                                    
                                    // 轻微调高明度(L)，让红黄暖色调在地图底色上更通透
                                    var rgb = hslToRgb(h, 1, 0.55);
                                    
                                    var canvasY = (grid_sz - 1) - r;
                                    var idx = (canvasY * grid_sz + c) * 4;
                                    imgData.data[idx] = rgb[0];
                                    imgData.data[idx+1] = rgb[1];
                                    imgData.data[idx+2] = rgb[2];
                                    // 适度提高基础不透明度，让高海拔暖色更扎实
                                    imgData.data[idx+3] = 140; 
                                }}
                            }}
                            ctx.putImageData(imgData, 0, 0);
                            
                            // 生成 Base64 并盖到 Leaflet 图层上
                            var imgUrl = canvas.toDataURL();
                            topoLayer = L.imageOverlay(imgUrl, data.gcj_bounds, {{opacity: 0.75}});
                            myMap.addLayer(topoLayer);
                            
                            topoBtn.innerHTML = '🌍 区域地形';
                            topoBtn.dataset.active = 'true';
                            setTopoNotice('');
                        }}).catch(err => {{
                            console.error(err);
                            topoBtn.innerHTML = '❌ 加载失败';
                            setTimeout(() => {{ topoBtn.innerHTML = '🌍 区域地形'; topoBtn.style.background = '#a0aec0'; }}, 2000);
                        }});
                        return;
                    }}
                    
                    // 已加载过，直接显示
                    myMap.addLayer(topoLayer);
                    topoBtn.dataset.active = 'true';
                    topoBtn.style.background = '#d69e2e';
                }}
            }};
        }}
    }}, 1000);
    '''

    control_html = f'''
    <div style="position: absolute; display: flex; gap: 10px; top: 10px; right: 10px; z-index:9999;">
        {topo_btn_html}
        {ele_btn_html}
        <button id="toggleLegend" onclick="toggleMapLegend()" style="
            padding: 8px 12px; background-color: #667eea; color: white; border: none;
            border-radius: 5px; cursor: pointer; font-weight: 600; font-size: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2); transition: all 0.3s ease;
        " onmouseover="this.style.transform='scale(1.05)'" onmouseout="this.style.transform='scale(1)'">
            🛣️ 路段类型
        </button>
    </div>
    
    <script>
    function toggleMapLegend() {{
        const legendDiv = document.getElementById('mapLegend');
        const btn = document.getElementById('toggleLegend');
        if (legendDiv.style.display === 'none') {{
            legendDiv.style.display = 'block';
            btn.style.background = '#38b000';
        }} else {{
            legendDiv.style.display = 'none';
            btn.style.background = '#667eea';
        }}
    }}
    {ele_js}
    </script>
    '''
    
    m.get_root().html.add_child(folium.Element(control_html))
    
    # === 完美填满容器且解决跨域请求的核心修复 ===
    import html
    
    # 1. 获取地图纯 HTML 内容
    html_data = m.get_root().render()
    
    # 2. 将 HTML 进行转义，以便安全地放入双引号中
    escaped_html = html.escape(html_data)
    
    # 3. 手动构建 iframe，使用 srcdoc 属性！
    # srcdoc 会让 iframe 完美继承父网页的域名，fetch 请求就不会被拦截了
    iframe_html = f'''
    <iframe srcdoc="{escaped_html}" 
            style="width: 100%; height: 100%; border: none; margin: 0; padding: 0; display: block;"
            allowfullscreen>
    </iframe>
    '''
    
    return iframe_html

# ========================
# API 端点
# ========================

@bp.route('/config', methods=['GET'])
def get_config():
    """获取配置"""
    config = load_config()
    return jsonify(config)

@bp.route('/config', methods=['POST'])
def set_config():
    """保存配置"""
    config = request.get_json() or {}
    save_config(config)
    return jsonify({"status": "ok"})

@bp.route('/geocode', methods=['POST'])
def geocode():
    """地理编码 (增强版：支持直接跳过坐标字符串)"""
    data = request.get_json() or {}
    address = data.get("address", "").strip()
    
    if not address:
        return jsonify({"error": "缺少地址"}), 400
    
    # 核心增强：如果是经纬度格式 (如 39.915,116.404)，直接原样返回作为坐标
    parts = address.split(',')
    if len(parts) == 2:
        try:
            lat = float(parts[0])
            lng = float(parts[1])
            return jsonify({
                "status": "ok", 
                "coords": f"{lat},{lng}", 
                "name": "当前定位位置",
                "address": address
            })
        except ValueError:
            pass # 不是有效的坐标格式，继续走百度查询逻辑
    
    coords = geocode_address(address)
    if coords:
        return jsonify({"status": "ok", **coords, "address": address})
    else:
        return jsonify({"error": "地理编码失败"}), 400

@bp.route('/topo', methods=['POST'])
def get_topo():
    """获取区域地形矩阵数据 (按需懒加载)"""
    data = request.get_json() or {}
    bounds = data.get('bounds')
    grid_sz = int(data.get('grid_size', 100))  # 留好空间，默认100x100
    
    if not bounds:
        return jsonify({"error": "Missing bounds"}), 400
        
    wlat_start = float(bounds['min_lat'])
    wlat_end = float(bounds['max_lat'])
    wlng_start = float(bounds['min_lng'])
    wlng_end = float(bounds['max_lng'])
    
    try:
        import srtm
        elevation_data = srtm.get_data()
        
        grid = []
        min_ele = float('inf')
        max_ele = float('-inf')
        
        # 均匀计算步长
        lat_step = (wlat_end - wlat_start) / max(1, grid_sz - 1)
        lng_step = (wlng_end - wlng_start) / max(1, grid_sz - 1)
        
        last_valid_ele = 0
        for i in range(grid_sz):
            glat = wlat_start + i * lat_step
            row = []
            for j in range(grid_sz):
                glng = wlng_start + j * lng_step
                val = elevation_data.get_elevation(glat, glng)
                
                # 填补盲区数据
                if val is None:
                    val = last_valid_ele
                else:
                    last_valid_ele = val
                    
                row.append(val)
                if val < min_ele: min_ele = val
                if val > max_ele: max_ele = val
            grid.append(row)
            
        if min_ele == float('inf'): 
            min_ele, max_ele = 0, 100
            
        # 计算该区域贴在 Leaflet 上的 GCJ02 边界
        sw_lat, sw_lng = wgs84_to_gcj02(wlng_start, wlat_start)
        ne_lat, ne_lng = wgs84_to_gcj02(wlng_end, wlat_end)
        
        return jsonify({
            "status": "ok",
            "grid": grid,
            "min_ele": min_ele,
            "max_ele": max_ele,
            "gcj_bounds": [[sw_lat, sw_lng], [ne_lat, ne_lng]]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route('/route', methods=['POST'])
def route():
    """计算路线"""
    t_backend_start = time.time() # 🟢 1. 记录后端开始总时间
    
    data = request.get_json() or {}
    origin_obj = data.get("origin")
    dest_obj = data.get("destination")
    waypoints_objs = data.get("waypoints", [])
    
    # 获取原始地址（用于历史记录）
    origin_address = data.get("origin_address")
    destination_address = data.get("destination_address")
    waypoint_addresses = data.get("waypoint_addresses", [])
    
    if not origin_obj or not dest_obj:
        return jsonify({"error": "缺少起点或终点"}), 400
    
    if isinstance(origin_obj, dict):
        origin_coords = origin_obj.get("coords")
    else:
        origin_coords = origin_obj

    if isinstance(dest_obj, dict):
        dest_coords = dest_obj.get("coords")
    else:
        dest_coords = dest_obj

    wp_coords = []
    for wp in waypoints_objs:
        if isinstance(wp, dict):
            wp_coords.append(wp.get("coords"))
        else:
            wp_coords.append(wp)
    if not wp_coords:
        wp_coords = None

    t_route_api_start = time.time() # 🟢 2. 记录请求百度API时间
    route_data = calculate_route(origin_coords, dest_coords, wp_coords)
    t_route_api = time.time() - t_route_api_start
    
    if route_data:
        t_cost_start = time.time() # 🟢 3. 记录成本计算时间
        config = data.get("config") or load_config()
        distance_km, duration_min, toll, oil_cost, other_cost, total_cost, breakdown, electric_cost, electric_total_cost = calculate_route_cost(route_data, config)
        t_cost = time.time() - t_cost_start
        
        # -----------------------------
        # 高程插值与查询
        # -----------------------------
        t_srtm_start = time.time() # 🟢 4. 记录SRTM高程拉取与插值时间
        elevation_data_list = []
        total_climb = 0.0
        max_ele = 0.0
        min_ele = 0.0
        cache_size_gb = 0.0
        cache_size_bytes = 0
        
        try:
            raw_points_bd09 = []
            steps = route_data.get("steps", [])
            for step in steps:
                path_str = step.get("path", "")
                if not path_str: continue
                for pt_str in path_str.split(";"):
                    if not pt_str.strip(): continue
                    lng_str, lat_str = pt_str.split(",")
                    lon, lat = float(lng_str), float(lat_str)
                    if not raw_points_bd09 or raw_points_bd09[-1] != (lon, lat):
                        raw_points_bd09.append((lon, lat))
            
            if raw_points_bd09 and 'srtm' in globals():
                # 转换坐标
                points_wgs84 = []
                for lon, lat in raw_points_bd09:
                    wgs_lat, wgs_lng = bd09_to_wgs84(lat, lon)
                    points_wgs84.append((wgs_lng, wgs_lat)) # lon, lat格式
                
                # 插值
                sampled_points = interpolate_line(points_wgs84, interval=100) # 采样间距100m
                
                # 初始化SRTM
                cache_dir = os.path.expanduser('~/.cache/srtm')
                os.makedirs(cache_dir, exist_ok=True)
                cache_size_bytes = check_and_clean_srtm_cache(cache_dir, limit_gb=10)
                
                elevation_data = srtm.get_data()
                
                last_ele = None
                distance_acc = 0.0
                elevations = []
                last_valid_ele = None 
                
                for i, (wgs_lon, wgs_lat) in enumerate(sampled_points):
                    ele = elevation_data.get_elevation(wgs_lat, wgs_lon)
                    
                    if ele is None:
                        if last_valid_ele is not None:
                            ele = last_valid_ele  
                        else:
                            ele = 0  
                    else:
                        last_valid_ele = ele  
                        
                    elevations.append(ele)
                    
                    if i > 0:
                        prev_lon, prev_lat = sampled_points[i-1]
                        dist = haversine_distance(prev_lon, prev_lat, wgs_lon, wgs_lat)
                        distance_acc += dist
                    
                    gcj_lat, gcj_lon = wgs84_to_gcj02(wgs_lon, wgs_lat)
                    
                    elevation_data_list.append({
                        "distance_km": round(distance_acc / 1000, 2),
                        "ele": ele,
                        "lat": wgs_lat,
                        "lng": wgs_lon,
                        "gcj_lat": gcj_lat,
                        "gcj_lng": gcj_lon
                    })
                    
                    if last_ele is not None and ele > last_ele:
                        total_climb += (ele - last_ele)
                    last_ele = ele
                
                if elevations:
                    max_ele = max(elevations)
                    min_ele = min(elevations)
                    
        except Exception as e:
            print(f"⚠️ 高程计算失败: {e}")

        t_srtm = time.time() - t_srtm_start

        # 生成地图URL
        map_url = generate_static_map(origin_coords, dest_coords, wp_coords)
        
        t_folium_start = time.time() # 🟢 5. 记录Folium地图渲染时间
        try:
            map_html = generate_folium_map(route_data, origin_obj, dest_obj, waypoints_objs, waypoint_addresses, elevation_data_list=elevation_data_list)
        except Exception as e:
            print(f"❌ 生成folium地图失败: {e}")
            map_html = None
        t_folium = time.time() - t_folium_start
        
        # 提取steps数据用于前端绘图
        steps = route_data.get("steps", [])
        steps_data = [{
            "distance": step.get("distance", 0),
            "duration": step.get("duration", 0),
            "road_type": step.get("road_type", 6),
            "instruction": step.get("instruction", "")
        } for step in steps]
        
        # 分析路段类型
        road_type_analysis = analyze_route_types(route_data)
        
        # 只取部分 elevation nodes 返回前端
        chart_elevation_list = []
        if elevation_data_list:
            step_size = max(1, len(elevation_data_list) // 3000)
            chart_elevation_list = elevation_data_list[::step_size]
        
        t_backend_total = time.time() - t_backend_start # 🟢 6. 计算后端总耗时
        
        result = {
            "status": "ok",
            "distance_km": distance_km,
            "duration_min": duration_min,
            "toll": round(toll, 2),
            "oil_cost": oil_cost,
            "other": other_cost,
            "total_cost": total_cost,
            "breakdown": breakdown,
            "routeSteps": steps_data,
            "roadTypeAnalysis": road_type_analysis,
            "map_url": map_url,
            "map_html": map_html,
            "electric_cost": electric_cost,
            "electric_total_cost": electric_total_cost,
            "origin_detail": origin_obj if isinstance(origin_obj, dict) else {},
            "dest_detail": dest_obj if isinstance(dest_obj, dict) else {},
            "waypoints_detail": [wp for wp in waypoints_objs if isinstance(wp, dict)],
            "elevation": {
                "profile": chart_elevation_list,
                "total_climb_m": round(total_climb, 1),
                "max_ele_m": round(max_ele, 1),
                "min_ele_m": round(min_ele, 1),
                "cache_size_mb": round(cache_size_bytes / (1024 * 1024), 2)
            },
            # 👇 将各阶段耗时精准传给前端
            "timing": {
                "backend_total": round(t_backend_total, 3),
                "route_api": round(t_route_api, 3),
                "cost_calc": round(t_cost, 3),
                "srtm_calc": round(t_srtm, 3),
                "folium_render": round(t_folium, 3)
            }
        }
        
        # 保存到历史记录（包含地址信息）
        history = load_history()
        history["trips"].append({
            "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "origin_address": origin_address or (origin_obj.get("name") if isinstance(origin_obj, dict) else origin_coords),
            "destination_address": destination_address or (dest_obj.get("name") if isinstance(dest_obj, dict) else dest_coords),
            "waypoint_addresses": waypoint_addresses or [],
            "distance_km": distance_km,
            "duration_min": duration_min,
            "toll": round(toll, 2),
            "oil_cost": oil_cost,
            "other": other_cost,
            "total_cost": total_cost,
            "breakdown": breakdown,
            "map_url": map_url
        })
        save_history(history)
        
        return jsonify(result)
    else:
        return jsonify({"error": "路线规划失败"}), 400

@bp.route('/history', methods=['GET'])
def get_history():
    """获取历史记录"""
    history = load_history()
    return jsonify(history)

@bp.route('/history', methods=['DELETE'])
def clear_history():
    """清除历史记录 - 需要登录"""
    from flask import session, jsonify
    
    # 检查登录状态
    if not session.get("logged_in"):
        return jsonify({"error": "需要登录后才能清除历史记录", "require_login": True}), 403
    
    try:
        save_history({"trips": []})
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": "清除历史记录失败", "message": str(e)}), 500

@bp.route('/favorites', methods=['GET'])
def get_favorites():
    """获取收藏记录"""
    favorites = load_favorites()
    favorites = attach_existing_favorite_images(favorites)
    return jsonify(favorites)

@bp.route('/favorites', methods=['POST'])
def toggle_favorite():
    """添加或删除收藏"""
    data = request.get_json() or {}
    trip_id = data.get("id") # 使用地址和距离的哈希作为 ID
    trip_data = data.get("trip")
    
    favorites = load_favorites()
    found_idx = -1
    
    if not trip_id:
        # 如果没传 ID，根据 origin, destination, distance 生成一个
        origin = trip_data.get("origin_address", "")
        dest = trip_data.get("destination_address", "")
        dist = trip_data.get("distance_km", 0)
        trip_id = hashlib.md5(f"{origin}{dest}{dist}".encode()).hexdigest()

    for idx, fav in enumerate(favorites["favorites"]):
        if fav.get("id") == trip_id:
            found_idx = idx
            break
            
    if found_idx >= 0:
        # 已存在，则删除（取消收藏）
        removed_fav = favorites["favorites"].pop(found_idx)
        delete_favorite_map_image(removed_fav)
        save_favorites(favorites)
        return jsonify({"status": "ok", "action": "removed", "id": trip_id})
    else:
        # 不存在，则添加
        new_fav = trip_data.copy()
        new_fav["id"] = trip_id
        new_fav["favorite_at"] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        image_file, image_url = cache_favorite_map_image(trip_id, new_fav.get("map_url"))
        if image_file and image_url:
            new_fav["map_image_file"] = image_file
            new_fav["map_image_url"] = image_url
        favorites["favorites"].insert(0, new_fav)
        save_favorites(favorites)
        return jsonify({"status": "ok", "action": "added", "id": trip_id})

@bp.route('/favorites/<fav_id>', methods=['DELETE'])
def delete_favorite(fav_id):
    """直接删除指定收藏"""
    favorites = load_favorites()
    removed_favs = [f for f in favorites["favorites"] if f.get("id") == fav_id]
    new_favs = [f for f in favorites["favorites"] if f.get("id") != fav_id]
    
    if len(new_favs) == len(favorites["favorites"]):
        return jsonify({"error": "未找到该收藏"}), 404

    for fav in removed_favs:
        delete_favorite_map_image(fav)
    favorites["favorites"] = new_favs
    save_favorites(favorites)
    return jsonify({"status": "ok"})

@bp.route('/favorite_images/<path:filename>', methods=['GET'])
def favorite_image(filename):
    """返回收藏时缓存的本地静态路线图"""
    return send_from_directory(FAVORITE_IMAGES_DIR, filename)

@bp.route('/map', methods=['POST'])
def get_map():
    """生成并返回路线地图"""
    data = request.get_json() or {}
    origin_obj = data.get("origin")
    dest_obj = data.get("destination")
    waypoints_objs = data.get("waypoints", [])
    
    if not origin_obj or not dest_obj:
        return jsonify({"error": "缺少起点或终点"}), 400

    if isinstance(origin_obj, dict):
        origin_coords = origin_obj.get("coords")
    else:
        origin_coords = origin_obj

    if isinstance(dest_obj, dict):
        dest_coords = dest_obj.get("coords")
    else:
        dest_coords = dest_obj

    wp_coords = []
    if waypoints_objs:
        for wp in waypoints_objs:
            if isinstance(wp, dict):
                wp_coords.append(wp.get("coords"))
            else:
                wp_coords.append(wp)
    if not wp_coords:
        wp_coords = None
    
    route_data = calculate_route(origin_coords, dest_coords, wp_coords)
    
    if not route_data:
        return jsonify({"error": "未找到合适的路线"}), 404
    
    # 生成Folium地图
    map_file = generate_folium_map(route_data, origin_obj, dest_obj, waypoints_objs)
    
    # 直接返回 HTML 文本
    return map_file, 200, {'Content-Type': 'text/html; charset=utf-8'}
