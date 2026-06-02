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
from flask import Blueprint, request, jsonify, send_file
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
        step_sz = max(1, len(elevation_data_list) // 500)
        chart_elevation_list = elevation_data_list[::step_sz]
        
        # 提取坐标点和对应的海拔高度
        coords = [[pt['gcj_lat'], pt['gcj_lng']] for pt in chart_elevation_list]
        elevations = [pt['ele'] for pt in chart_elevation_list]
        
        min_ele = min(elevations)
        max_ele = max(elevations)
        
        # 防止平路导致除数为0的错误
        if min_ele == max_ele:
            max_ele += 1
            
        # 定义与前端 Chart.js 完全对应的彩虹色阶
        # 紫(最低) -> 蓝 -> 青 -> 绿 -> 黄 -> 橙 -> 红(最高)
        colormap = cm.LinearColormap(
            colors=['#8b00ff', '#0000ff', '#00ffff', '#00ff00', '#ffff00', '#ff7f00', '#ff0000'],
            vmin=min_ele,
            vmax=max_ele
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
    
    # 添加控制面板（显示/隐藏路段类型按钮）
# 获取底图的 JS 变量名
    map_var_name = m.get_name()
    
    # 如果有海拔数据，动态生成海拔开关按钮和 JS 逻辑
    ele_btn_html = ""
    ele_js = ""
    if ele_var_name:
        ele_btn_html = f'''
        <button id="toggleEleBtn" onclick="toggleElevationLayer()" style="
            padding: 8px 12px;
            background-color: #e53e3e;
            color: white;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-weight: 600;
            font-size: 12px;
            display: block;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
            transition: all 0.3s ease;
        " onmouseover="this.style.transform='scale(1.05)'" 
           onmouseout="this.style.transform='scale(1)'">
            🌈 海拔开关
        </button>
        '''
        ele_js = f'''
        // 延迟执行以确保 Leaflet 地图和图例完全加载
        setTimeout(function() {{
            var eleBtn = document.getElementById('toggleEleBtn');
            if (!eleBtn) return;
            eleBtn.dataset.active = 'true'; // 初始状态为开启
            
            // 【核心逻辑】寻找并控制海拔图例
            var colormapContainer = null;
            var allSvgs = document.querySelectorAll('svg');
            for (var i = 0; i < allSvgs.length; i++) {{
                // 找到带有我们标题文字的 SVG
                if (allSvgs[i].textContent.includes('海拔高度')) {{
                    // 获取 Folium 自动生成的图例外层容器
                    colormapContainer = allSvgs[i].closest('.leaflet-control') || allSvgs[i];
                    
                    // 1. 解决重叠：将图例往下推 50px
                    colormapContainer.style.marginTop = '50px';
                    
                    // 2. 解决响应式缩放：利用 CSS Transform 缩放适配小屏幕
                    colormapContainer.style.transformOrigin = 'top right';
                    if (window.innerWidth <= 768) {{
                        colormapContainer.style.transform = 'scale(0.75)';
                    }} else {{
                        colormapContainer.style.transform = 'scale(1)';
                    }}
                    
                    // 监听窗口大小改变，动态缩放图例
                    window.addEventListener('resize', function() {{
                        if (window.innerWidth <= 768) {{
                            colormapContainer.style.transform = 'scale(0.75)';
                        }} else {{
                            colormapContainer.style.transform = 'scale(1)';
                        }}
                    }});
                    
                    break;
                }}
            }}
            
            // 绑定按钮点击事件
            window.toggleElevationLayer = function() {{
                var myMap = {map_var_name};
                var eleLayer = {ele_var_name};
                
                if (eleBtn.dataset.active === 'true') {{
                    // 关闭图层和图例
                    myMap.removeLayer(eleLayer);
                    eleBtn.dataset.active = 'false';
                    eleBtn.style.background = '#a0aec0'; // 变灰
                    if (colormapContainer) colormapContainer.style.display = 'none'; // 隐藏图例
                }} else {{
                    // 开启图层和图例
                    myMap.addLayer(eleLayer);
                    eleBtn.dataset.active = 'true';
                    eleBtn.style.background = '#e53e3e'; // 变红
                    if (colormapContainer) colormapContainer.style.display = 'block'; // 显示图例
                }}
            }};
        }}, 1000);
        '''

    # 构建并排的两个控制按钮（Flex 布局）
    control_html = f'''
    <div style="position: absolute; display: flex; gap: 10px;
                top: 10px; right: 10px; z-index:9999;">
        {ele_btn_html}
        <button id="toggleLegend" onclick="toggleMapLegend()" style="
            padding: 8px 12px;
            background-color: #667eea;
            color: white;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-weight: 600;
            font-size: 12px;
            display: block;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
            transition: all 0.3s ease;
        " onmouseover="this.style.transform='scale(1.05)'" 
           onmouseout="this.style.transform='scale(1)'">
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
    
    # 返回HTML字符串
    return m._repr_html_()

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

@bp.route('/route', methods=['POST'])
def route():
    """计算路线"""
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

    route_data = calculate_route(origin_coords, dest_coords, wp_coords)
    
    if route_data:
        config = data.get("config") or load_config()
        distance_km, duration_min, toll, oil_cost, other_cost, total_cost, breakdown, electric_cost, electric_total_cost = calculate_route_cost(route_data, config)
        
        # -----------------------------
        # 高程插值与查询
        # -----------------------------
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
                # SRTM 默认会缓存到 ~/.cache/srtm，我们查验并清理这个目录
                cache_dir = os.path.expanduser('~/.cache/srtm')
                os.makedirs(cache_dir, exist_ok=True)
                cache_size_bytes = check_and_clean_srtm_cache(cache_dir, limit_gb=10)
                
                elevation_data = srtm.get_data()
                
                last_ele = None
                distance_acc = 0.0
                elevations = []
                
                for i, (wgs_lon, wgs_lat) in enumerate(sampled_points):
                    ele = elevation_data.get_elevation(wgs_lat, wgs_lon)
                    if ele is None: ele = 0
                    elevations.append(ele)
                    
                    if i > 0:
                        prev_lon, prev_lat = sampled_points[i-1]
                        dist = haversine_distance(prev_lon, prev_lat, wgs_lon, wgs_lat)
                        distance_acc += dist
                    
                    # 额外转换一份 GCJ02 提供给 Folium 从 WGS84 投影到底图
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

        # 生成地图URL
        map_url = generate_static_map(origin_coords, dest_coords, wp_coords)
        
        # 生成folium交互式地图（传入waypoints和waypoint_addresses, 并带高程数据用于上色）
        try:
            map_html = generate_folium_map(route_data, origin_obj, dest_obj, waypoints_objs, waypoint_addresses, elevation_data_list=elevation_data_list)
        except Exception as e:
            print(f"❌ 生成folium地图失败: {e}")
            map_html = None
        
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
        
        # 只取部分 elevation nodes 返回前端（抽稀到最多300个点绘图，否则前端太卡）
        chart_elevation_list = []
        if elevation_data_list:
            step_size = max(1, len(elevation_data_list) // 300)
            chart_elevation_list = elevation_data_list[::step_size]
        
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
        favorites["favorites"].pop(found_idx)
        save_favorites(favorites)
        return jsonify({"status": "ok", "action": "removed", "id": trip_id})
    else:
        # 不存在，则添加
        new_fav = trip_data.copy()
        new_fav["id"] = trip_id
        new_fav["favorite_at"] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        favorites["favorites"].insert(0, new_fav)
        save_favorites(favorites)
        return jsonify({"status": "ok", "action": "added", "id": trip_id})

@bp.route('/favorites/<fav_id>', methods=['DELETE'])
def delete_favorite(fav_id):
    """直接删除指定收藏"""
    favorites = load_favorites()
    new_favs = [f for f in favorites["favorites"] if f.get("id") != fav_id]
    
    if len(new_favs) == len(favorites["favorites"]):
        return jsonify({"error": "未找到该收藏"}), 404
        
    favorites["favorites"] = new_favs
    save_favorites(favorites)
    return jsonify({"status": "ok"})

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