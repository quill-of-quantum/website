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

# ================= 配置 =================
# 支持相对路径和绝对路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(SCRIPT_DIR, 'output/response.json')
OUTPUT_HTML = os.path.join(SCRIPT_DIR, 'output/route_visualization_fixed.html')
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
    origin = result.get('origin', {})
    destination = result.get('destination', {})
    
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
    
    print(f"✅ 成功绘制 {valid_segments} 个路段\n")
    
    # ================= 起点标记 =================
    try:
        folium.Marker(
            location=[origin_lat, origin_lng],
            popup='<b>起点</b>',
            tooltip='出发地',
            icon=folium.Icon(color='green', icon='play', prefix='fa')
        ).add_to(m)
        print("✅ 起点标记已添加")
    except Exception as e:
        print(f"⚠️ 起点标记添加失败: {e}")
    
    # ================= 终点标记 =================
    try:
        folium.Marker(
            location=[dest_lat, dest_lng],
            popup='<b>终点</b>',
            tooltip='目的地',
            icon=folium.Icon(color='red', icon='stop', prefix='fa')
        ).add_to(m)
        print("✅ 终点标记已添加")
    except Exception as e:
        print(f"⚠️ 终点标记添加失败: {e}")
    
    # ================= 添加图例 =================
    legend_html = '''
    <div style="position: fixed; 
                bottom: 50px; right: 50px; width: 200px; height: auto;
                background-color: white; border:2px solid grey; z-index:9999; 
                font-size:12px; padding: 10px; border-radius: 5px;
                box-shadow: 0 0 15px rgba(0,0,0,0.2);">
    <p style="margin: 0 0 10px 0; font-weight: bold;">🛣️ 路段类型</p>
    '''
    
    for road_type in sorted(road_type_colors.keys()):
        color = road_type_colors[road_type]
        name = road_type_names.get(road_type, '其他')
        legend_html += f'<p style="margin: 5px 0;"><i style="background:{color}; width: 15px; height: 2px; display: inline-block; margin-right: 5px;"></i>{name}</p>'
    
    legend_html += '</div>'
    
    m.get_root().html.add_child(folium.Element(legend_html))
    print("✅ 图例已添加")
    
    # ================= 统计信息面板 =================
    stats_html = f'''
    <div style="position: fixed; 
                top: 10px; left: 10px; width: 250px; height: auto;
                background-color: white; border:2px solid #667eea; z-index:9999; 
                font-size:13px; padding: 15px; border-radius: 5px;
                box-shadow: 0 0 15px rgba(0,0,0,0.2);">
    <p style="margin: 0 0 10px 0; font-weight: bold; color: #667eea;">🗺️ 路线统计</p>
    <p style="margin: 5px 0;"><b>📏 总距离:</b> {route.get('distance', 0) / 1000:.2f} km</p>
    <p style="margin: 5px 0;"><b>⏱️ 耗时:</b> {route.get('duration', 0) / 60:.1f} 分钟</p>
    <p style="margin: 5px 0;"><b>💰 过路费:</b> ¥{route.get('toll', 0)}</p>
    <p style="margin: 5px 0;"><b>🛣️ 路段数:</b> {len(steps)} 段</p>
    </div>
    '''
    
    m.get_root().html.add_child(folium.Element(stats_html))
    print("✅ 统计信息已添加")
    
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
        print("  • 不同颜色表示不同路段类型")
        print("  • 绿色标记为起点，红色标记为终点")
        print("  • 点击路段可查看详细信息")
        print("  • 右下角有路段类型图例")
        print("  • 左上角显示路线统计信息")
    else:
        print("\n❌ 处理失败，请检查输入文件和错误信息")
        sys.exit(1)
