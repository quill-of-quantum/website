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
from flask import Blueprint, request, jsonify, send_file
from datetime import datetime, timezone

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

def geocode_address(address, search_region="全国", depth=0):
    """
    使用百度地图地点检索 API 将地名转换为经纬度
    返回: 包含 coords/name/province/city/address 的字典，或 None
    """
    if depth > 2:
        print(f"❌ 递归层数过深，放弃解析 ({address})")
        return None

    api_path = "/place/v3/region"
    
    params = {
        "query": address,
        "region": search_region,
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
        
        if data.get("status") == 0 and data.get("results"):
            result_type = data.get("result_type", "")

            if result_type == "city_type":
                top_city = data["results"][0].get("name")
                print(f"⚠️ [{address}] 触发城市列表，自动重定向至最热城市: {top_city}")
                return geocode_address(address, search_region=top_city, depth=depth + 1)

            res = data["results"][0]
            location = res.get("location")
            if location:
                return {
                    "coords": f"{location['lat']},{location['lng']}",
                    "name": res.get("name"),
                    "province": res.get("province", ""),
                    "city": res.get("city", ""),
                    "address": res.get("address", "")
                }
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
# 坐标转换函数
# ========================

def bd09_to_wgs84(bd_lat, bd_lng):
    """
    将百度坐标 (BD09) 转换为 WGS84 坐标
    """
    x_pi = 3.14159265358979324 * 3000.0 / 180.0
    z = math.sqrt(bd_lng * bd_lng + bd_lat * bd_lat) + 0.00002 * math.sin(bd_lat * x_pi)
    theta = math.atan2(bd_lat, bd_lng) + 0.000003 * math.cos(bd_lng * x_pi)
    wgs_lng = z * math.cos(theta) - 0.0065
    wgs_lat = z * math.sin(theta) - 0.006
    return wgs_lat, wgs_lng

def generate_folium_map(route_data, origin_info, destination_info, waypoints_info=None, waypoint_addresses=None):
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
    
    # 转换坐标到 WGS84
    origin_lat, origin_lng = bd09_to_wgs84(origin_lat, origin_lng)
    dest_lat, dest_lng = bd09_to_wgs84(dest_lat, dest_lng)
    
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
    
    for idx, step in enumerate(steps):
        try:
            distance_km = step.get('distance', 0) / 1000
            duration_min = step.get('duration', 0) / 60
            road_type = int(step.get('road_type', 6))
            instruction = step.get('instruction', '').replace('<b>', '').replace('</b>', '')
            
            # 使用 path 字段而不是 polyline
            polyline = step.get('path', '')
            if not polyline:
                continue
            
            # 解析坐标：格式为 "lng,lat;lng,lat;..."
            try:
                coords = []
                for point_str in polyline.split(';'):
                    if point_str.strip():
                        lng, lat = point_str.split(',')
                        # 转换百度坐标到 WGS84
                        wgs_lat, wgs_lng = bd09_to_wgs84(float(lat), float(lng))
                        coords.append([wgs_lat, wgs_lng])
                
                if len(coords) < 2:
                    continue
                
                color = road_type_colors.get(road_type, '#666666')
                road_name = road_type_names.get(road_type, '其他')
                
                # 添加路段线条
                folium.PolyLine(
                    coords,
                    color=color,
                    weight=3,
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
                wp_lat, wp_lng = bd09_to_wgs84(wp_lat, wp_lng)
                
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
    if waypoints:
        for wp in waypoints:
            try:
                wp_parts = wp.split(',')
                wp_lat, wp_lng = float(wp_parts[0]), float(wp_parts[1])
                wp_lat, wp_lng = bd09_to_wgs84(wp_lat, wp_lng)
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
                        wgs_lat, wgs_lng = bd09_to_wgs84(float(lat), float(lng))
                        all_lats.append(wgs_lat)
                        all_lngs.append(wgs_lng)
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
    control_html = '''
    <div style="position: fixed; 
                top: 10px; right: 10px; z-index:10000;">
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
        " onmouseover="this.style.background='#764ba2'; this.style.transform='scale(1.05)'" 
           onmouseout="this.style.background='#667eea'; this.style.transform='scale(1)'">
            🛣️ 路段类型
        </button>
    </div>
    
    <script>
    function toggleMapLegend() {
        const legendDiv = document.getElementById('mapLegend');
        const btn = document.getElementById('toggleLegend');
        if (legendDiv.style.display === 'none') {
            legendDiv.style.display = 'block';
            btn.style.background = '#38b000';
        } else {
            legendDiv.style.display = 'none';
            btn.style.background = '#667eea';
        }
    }
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
    """地理编码"""
    data = request.get_json() or {}
    address = data.get("address")
    
    if not address:
        return jsonify({"error": "缺少地址"}), 400
    
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
        
        # 生成地图URL
        map_url = generate_static_map(origin_coords, dest_coords, wp_coords)
        
        # 生成folium交互式地图（传入waypoints和waypoint_addresses）
        try:
            map_html = generate_folium_map(route_data, origin_obj, dest_obj, waypoints_objs, waypoint_addresses)
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
            "electric_total_cost": electric_total_cost
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