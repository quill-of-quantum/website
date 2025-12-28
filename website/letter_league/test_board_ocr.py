"""
功能: 识别游戏棋盘(board)上已放置的字母，自动剔除噪点
用法: python test_board_ocr.py  
前置: 需要 ./output/board.png 文件 (由 test_orgin_segmentation.py 生成)
输出: ./output/board_result_v3.png (标注图，蓝叉=噪点，红字=强制识别)
"""

import cv2
import numpy as np
import easyocr
import os
import shutil
import math

# 1. 初始化
reader = easyocr.Reader(['en'], gpu=False, verbose=False)

img_path = "./output/board.png"
if not os.path.exists(img_path):
    print("❌ 找不到图片")
    exit()

img = cv2.imread(img_path)
h, w, _ = img.shape

# ==========================================================
# 步骤 1: 定位 (Dark Ink Extraction)
# ==========================================================
print("🚀 1. 开始定位黑色图块...")
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
_, binary_finder = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)
kernel_finder = np.ones((3,3), np.uint8)
binary_finder = cv2.dilate(binary_finder, kernel_finder, iterations=2)

cnts, _ = cv2.findContours(binary_finder, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

raw_tiles = []
for c in cnts:
    x, y, cw, ch = cv2.boundingRect(c)
    # 基础过滤
    if not (10 < cw < 150 and 10 < ch < 150): continue
    ratio = cw / float(ch)
    if not (0.2 < ratio < 2.0): continue
    raw_tiles.append((x, y, cw, ch))

print(f"🔍 初步找到 {len(raw_tiles)} 个目标")

# ==========================================================
# 步骤 2: 剔除离群点 (Outlier Removal) - 新增核心逻辑
# ==========================================================
final_tiles = []
debug_img_outlier = img.copy()

if len(raw_tiles) > 1:
    # 1. 计算平均方块大小 (用作距离单位)
    avg_size = sum([max(t[2], t[3]) for t in raw_tiles]) / len(raw_tiles)
    
    # 设定阈值：如果最近的邻居距离超过 2.5 个格子宽，认为是噪点
    # (Scrabble 允许对角线相邻吗？通常不允许，但视觉上2.5倍足够包容对角线了)
    distance_threshold = avg_size * 2.5
    
    print(f"📏 平均方块大小: {avg_size:.1f}px, 连通阈值: {distance_threshold:.1f}px")

    for i, t1 in enumerate(raw_tiles):
        c1_x = t1[0] + t1[2] // 2
        c1_y = t1[1] + t1[3] // 2
        
        # 寻找最近邻居的距离
        min_dist = float('inf')
        
        for j, t2 in enumerate(raw_tiles):
            if i == j: continue # 不和自己比
            
            c2_x = t2[0] + t2[2] // 2
            c2_y = t2[1] + t2[3] // 2
            
            # 欧几里得距离
            dist = math.sqrt((c1_x - c2_x)**2 + (c1_y - c2_y)**2)
            if dist < min_dist:
                min_dist = dist
        
        # 判定
        if min_dist < distance_threshold:
            final_tiles.append(t1)
        else:
            print(f"🗑️ 剔除离群点 @ ({t1[0]},{t1[1]}), 最近邻居距离: {min_dist:.1f}")
            # 画蓝色叉叉表示被剔除
            cv2.drawMarker(debug_img_outlier, (c1_x, c1_y), (255, 0, 0), cv2.MARKER_CROSS, 20, 2)
else:
    # 如果一共就1个或0个，没法算邻居，直接保留 (或者直接认为空)
    final_tiles = raw_tiles

# 排序
final_tiles = sorted(final_tiles, key=lambda b: (b[1], b[0]))
print(f"✅ 剔除后剩余 {len(final_tiles)} 个有效字母块")

# ==========================================================
# 步骤 3: 识别 + 排除法兜底 (Recognition)
# ==========================================================
results = []
debug_img = img.copy()

# 把刚才剔除的标记也画在最终图上，方便调试
for t in raw_tiles:
    if t not in final_tiles:
        cx, cy = t[0]+t[2]//2, t[1]+t[3]//2
        cv2.drawMarker(debug_img, (cx, cy), (255, 0, 0), cv2.MARKER_CROSS, 20, 2)

correction_map = {'0': 'O', '8': 'B', '6': 'G', '5': 'S', '1': 'I', '2': 'Z'}

for i, (x, y, cw, ch) in enumerate(final_tiles):
    # 切片
    center_x, center_y = x + cw // 2, y + ch // 2
    size = max(cw, ch) + 5
    half = size // 2
    y1, y2 = max(0, center_y - half), min(h, center_y + half)
    x1, x2 = max(0, center_x - half), min(w, center_x + half)
    
    roi = img[y1:y2, x1:x2]
    roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    
    # 二值化 + 瘦身 (针对 B/O)
    _, roi_binary = cv2.threshold(roi_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel_thin = np.ones((2,2), np.uint8)
    roi_thin = cv2.erode(roi_binary, kernel_thin, iterations=1)
    roi_padded = cv2.copyMakeBorder(roi_thin, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=(0,0,0))
    
    # 识别
    char = ""
    safe_allowlist = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    
    try:
        res = reader.readtext(roi_padded, detail=0, allowlist=safe_allowlist)
        if res:
            raw = res[0].upper()
            if raw in correction_map: char = correction_map[raw]
            elif len(raw) > 1 and raw[0].isalpha(): char = raw[0]
            elif raw.isalpha(): char = raw
    except: pass

    # --- 兜底逻辑 ---
    if not char:
        print(f"Tile {i}: OCR failed -> Force 'O'")
        char = 'O'
    elif char == '0':
        char = 'O'
        
    results.append(char)
    
    # 绘图
    color = (0, 0, 255) if not res else (0, 255, 0)
    cv2.rectangle(debug_img, (x1, y1), (x2, y2), (0, 255, 0), 2)
    cv2.putText(debug_img, char, (x1, y1-5), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)

cv2.imwrite("./output/board_result_v3.png", debug_img)
print("-" * 30)
print(f"🎉 最终结果: {' '.join(results)}")
print(f"请检查: ./output/board_result_v3.png (蓝叉=被剔除的噪点, 红字=强制认定的O)")