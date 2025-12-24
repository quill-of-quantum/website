# -*- coding: utf-8 -*-
import json
import folium
import os
import sys
import math

def get_road_name(road_type):
    """获取道路类型名称"""
    names = {0: '高速路', 1: '城市高速', 2: '国道', 3: '省道', 4: '县道', 5: '乡道', 6: '其他'}
    return names.get(road_type, '其他')

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

def gcj02_to_wgs84(lat, lng):
    """
    将GCJ02(高德/谷歌中国)坐标转换为WGS84坐标
    """
    dLat = transformLat(lng - 105.0, lat - 35.0)
    dLng = transformLng(lng - 105.0, lat - 35.0)
    radLat = lat / 180.0 * math.pi
    magic = math.sin(radLat)
    magic = 1 - 0.00669342162296594323 * magic * magic
    sqrtMagic = math.sqrt(magic)
    dLat = (dLat * 180.0) / ((6378245.0 * (1 - 0.00669342162296594323)) / (magic * sqrtMagic) * math.pi)
    dLng = (dLng * 180.0) / (6378245.0 / sqrtMagic * math.cos(radLat) * math.pi)
    mgLat = lat - dLat
    mgLng = lng - dLng
    return mgLat, mgLng

def transformLat(x, y):
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret

def transformLng(x, y):
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret

def wgs84_direct(lat, lng):
    """
    直接使用原始坐标（假设已是WGS84）
    """
    return lat, lng

def smooth_coords(coords, window_size=3):
    """
    对坐标进行移动平均平滑处理，减少离散点的影响
    """
    if len(coords) <= window_size:
        return coords
    
    smoothed = []
    half_window = window_size // 2
    
    for i in range(len(coords)):
        start = max(0, i - half_window)
        end = min(len(coords), i + half_window + 1)
        
        avg_lat = sum(c[0] for c in coords[start:end]) / len(coords[start:end])
        avg_lng = sum(c[1] for c in coords[start:end]) / len(coords[start:end])
        
        smoothed.append([avg_lat, avg_lng])
    
    return smoothed

# ================= 配置 =================
# 支持相对路径和绝对路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(SCRIPT_DIR, './output/route_test.json')
OUTPUT_HTML = os.path.join(SCRIPT_DIR, './output/route_visualization_fixed.html')
# =======================================

def visualize_route(file_path):
    """
    可视化百度地图路线规划结果
    """
    print(f"📂 脚本目录: {SCRIPT_DIR}")
    print(f"📖 输入文件: {file_path}")
    print(f"📁 输出文件: {OUTPUT_HTML}")
    print("正在读取数据...\n")
    
    if not os.path.exists(file_path):
        print(f"❌ 文件不存在: {file_path}")
        return False
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ 读取JSON失败: {e}")
        return False
    
    # 检查数据结构
    if data.get('status') != 0:
        print(f"❌ API 返回错误: {data.get('message')}")
        return False
    
    result = data.get('result', {})
    routes = result.get('routes', [])
    
    if not routes:
        print("❌ 没有找到路线数据")
        return False
    
    route = routes[0]
    origin = route.get('origin', {})
    destination = route.get('destination', {})
    
    print(f"DEBUG - Origin: {origin}")
    print(f"DEBUG - Destination: {destination}")
    
    origin_lat = origin.get('lat')
    origin_lng = origin.get('lng')
    dest_lat = destination.get('lat')
    dest_lng = destination.get('lng')
    
    if not all([origin_lat, origin_lng, dest_lat, dest_lng]):
        print("❌ 坐标数据不完整")
        return False
    
    # 转换坐标到 WGS84
    origin_lat, origin_lng = bd09_to_wgs84(origin_lat, origin_lng)
    dest_lat, dest_lng = bd09_to_wgs84(dest_lat, dest_lng)
    
    # 计算地图中心
    center_lat = (origin_lat + dest_lat) / 2
    center_lng = (origin_lng + dest_lng) / 2
    
    print(f"📍 起点: ({origin_lat}, {origin_lng})")
    print(f"🏁 终点: ({dest_lat}, {dest_lng})")
    print(f"🎯 地图中心: ({center_lat}, {center_lng})")
    print(f"📏 总距离: {route.get('distance', 0) / 1000:.2f} km")
    print(f"⏱️ 预计耗时: {route.get('duration', 0) / 60:.1f} 分钟")
    print(f"💰 过路费: ¥{route.get('toll', 0)}\n")
    
    # 创建地图
    try:
        m = folium.Map(
            location=[center_lat, center_lng],
            zoom_start=10,
            control_scale=True,
            tiles='https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}',
            attr='Google'
        )
        print("✅ 地图对象创建成功")
    except Exception as e:
        print(f"❌ 创建地图失败: {e}")
        return False
    
    # ================= 路段类型配置 =================
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
    
    # ================= 绘制各路段 =================
    steps = route.get('steps', [])
    print(f"📊 共 {len(steps)} 个路段\n")
    
    # 定义转换方法
    conversion_methods = {
        'BD09': {'func': bd09_to_wgs84, 'color': '#FF0000', 'name': 'BD09转换'},
        'GCJ02': {'func': gcj02_to_wgs84, 'color': '#0000FF', 'name': 'GCJ02转换'},
        'WGS84': {'func': wgs84_direct, 'color': '#00AA00', 'name': '原始WGS84'}
    }
    
    for method_name, method_info in conversion_methods.items():
        print(f"\n🔄 正在绘制 {method_info['name']} 路线...")
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
                            # 转换坐标
                            conv_lat, conv_lng = method_info['func'](float(lat), float(lng))
                            coords.append([conv_lat, conv_lng])
                    
                    if len(coords) < 2:
                        continue
                    
                    # 对坐标进行平滑处理
                    coords = smooth_coords(coords, window_size=3)
                    
                    road_name = road_type_names.get(road_type, '其他')
                    
                    # 添加路段线条
                    folium.PolyLine(
                        coords,
                        color=method_info['color'],
                        weight=2,
                        opacity=0.7,
                        popup=f"<b>{method_info['name']} - 第 {idx + 1} 段 - {road_name}</b><br/>{distance_km:.2f} km | {duration_min:.0f} 分钟",
                        tooltip=f"{method_info['name']}: {road_name}"
                    ).add_to(m)
                    
                    valid_segments += 1
                    
                except ValueError as e:
                    print(f"⚠️ 路段 {idx + 1} 坐标转换失败: {e}")
                    continue
            
            except Exception as e:
                print(f"⚠️ 路段 {idx + 1} 处理异常: {e}")
                continue
        
        print(f"✅ {method_info['name']} 成功绘制 {valid_segments} 个路段")
    
    # ================= 起点和终点标记 =================
    marker_colors = {'BD09': 'green', 'GCJ02': 'blue', 'WGS84': 'gray'}
    
    for method_name, method_info in conversion_methods.items():
        try:
            origin_lat_conv, origin_lng_conv = method_info['func'](origin_lat, origin_lng)
            dest_lat_conv, dest_lng_conv = method_info['func'](dest_lat, dest_lng)
            
            # 起点
            folium.Marker(
                location=[origin_lat_conv, origin_lng_conv],
                popup=f'<b>起点 ({method_info["name"]})</b>',
                tooltip=f'{method_info["name"]} 起点',
                icon=folium.Icon(color=marker_colors.get(method_name, 'gray'), icon='play', prefix='fa')
            ).add_to(m)
            
            # 终点
            folium.Marker(
                location=[dest_lat_conv, dest_lng_conv],
                popup=f'<b>终点 ({method_info["name"]})</b>',
                tooltip=f'{method_info["name"]} 终点',
                icon=folium.Icon(color=marker_colors.get(method_name, 'gray'), icon='stop', prefix='fa')
            ).add_to(m)
        except Exception as e:
            print(f"⚠️ {method_info['name']} 标记添加失败: {e}")
    
    print("✅ 所有标记已添加")
    
    # ================= 添加图例 =================
    legend_html = '''
    <div style="position: fixed; 
                bottom: 50px; right: 50px; width: 280px; height: auto;
                background-color: white; border:2px solid grey; z-index:9999; 
                font-size:12px; padding: 10px; border-radius: 5px;
                box-shadow: 0 0 15px rgba(0,0,0,0.2);">
    <p style="margin: 0 0 10px 0; font-weight: bold;">🗺️ 坐标格式对比</p>
    <p style="margin: 5px 0;"><i style="background:#FF0000; width: 15px; height: 2px; display: inline-block; margin-right: 5px;"></i><b>红色</b>: BD09转换</p>
    <p style="margin: 5px 0;"><i style="background:#0000FF; width: 15px; height: 2px; display: inline-block; margin-right: 5px;"></i><b>蓝色</b>: GCJ02转换</p>
    <p style="margin: 5px 0;"><i style="background:#00AA00; width: 15px; height: 2px; display: inline-block; margin-right: 5px;"></i><b>绿色</b>: 原始WGS84</p>
    </div>
    '''
    
    m.get_root().html.add_child(folium.Element(legend_html))
    print("✅ 图例已添加")
    
    # ================= 保存地图 =================
    output_dir = os.path.dirname(OUTPUT_HTML)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"✅ 输出目录已创建: {output_dir}")
    
    try:
        m.save(OUTPUT_HTML)
        print(f"\n✅ 地图已保存到: {OUTPUT_HTML}")
        print(f"📌 文件大小: {os.path.getsize(OUTPUT_HTML) / 1024:.2f} KB")
        return True
    except Exception as e:
        print(f"❌ 保存地图失败: {e}")
        return False

if __name__ == '__main__':
    print("=" * 60)
    print("🗺️  路线可视化工具")
    print("=" * 60 + "\n")
    
    success = visualize_route(INPUT_FILE)
    
    if success:
        print("\n" + "=" * 60)
        print("✅ 处理完成！")
        print("=" * 60)
        print("\n💡 使用说明:")
        print("  • 在浏览器中打开生成的 HTML 文件")
        print("  • 不同颜色表示不同坐标格式转换结果")
        print("  • 红色线=BD09转换，蓝色线=GCJ02转换，绿色线=原始WGS84")
        print("  • 点击路段可查看详细信息")
        print("  • 右下角有坐标格式对比图例")
    else:
        print("\n❌ 处理失败，请检查输入文件和错误信息")
        sys.exit(1)
