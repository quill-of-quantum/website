# /home/bbdwz/projects/website/map_api.py
# -*- coding: utf-8 -*-
import os
import json
import requests
import hashlib
import urllib.parse
from flask import Blueprint, request, jsonify
from datetime import datetime

bp = Blueprint('map', __name__, url_prefix='/api/map')

# ========================
# 配置
# ========================
MAP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(MAP_DIR, "config.json")
HISTORY_FILE = os.path.join(MAP_DIR, "history.json")

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

def geocode_address(address):
    """
    使用百度地图 Geocoding API 将地名转换为经纬度
    返回: "latitude,longitude" 或 None 如果失败
    """
    api_path = "/geocoding/v3/"
    
    params = {
        "address": address,
        "output": "json",
        "ak": AK,
    }
    
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
            location = data["result"]["location"]
            return f"{location['lat']},{location['lng']}"
    except Exception as e:
        print(f"❌ 地理编码失败 ({address}): {e}")
    
    return None

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
    """地理编码"""
    data = request.get_json() or {}
    address = data.get("address")
    
    if not address:
        return jsonify({"error": "缺少地址"}), 400
    
    coords = geocode_address(address)
    if coords:
        return jsonify({"status": "ok", "coords": coords, "address": address})
    else:
        return jsonify({"error": "地理编码失败"}), 400

@bp.route('/route', methods=['POST'])
def route():
    """计算路线"""
    data = request.get_json() or {}
    origin = data.get("origin")
    destination = data.get("destination")
    waypoints = data.get("waypoints")
    
    # 获取原始地址（用于历史记录）
    origin_address = data.get("origin_address")
    destination_address = data.get("destination_address")
    waypoint_addresses = data.get("waypoint_addresses", [])
    
    if not origin or not destination:
        return jsonify({"error": "缺少起点或终点"}), 400
    
    route_data = calculate_route(origin, destination, waypoints)
    
    if route_data:
        config = load_config()
        distance_km, duration_min, toll, oil_cost, other_cost, total_cost, breakdown, electric_cost, electric_total_cost = calculate_route_cost(route_data, config)
        
        # 生成地图URL
        map_url = generate_static_map(origin, destination, waypoints)
        
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
            "electric_cost": electric_cost,
            "electric_total_cost": electric_total_cost
        }
        
        # 保存到历史记录（包含地址信息）
        history = load_history()
        history["trips"].append({
            "timestamp": datetime.now().isoformat(),
            "origin_address": origin_address or origin,
            "destination_address": destination_address or destination,
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