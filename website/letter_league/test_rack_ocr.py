import os
import cv2
import easyocr
import numpy as np

# ==========================================
# 1. 初始化 (简单直接)
# ==========================================
# gpu=False 强制使用 CPU，绝对稳定，不会崩溃
# verbose=False 关闭啰嗦的日志
print("正在加载 EasyOCR 模型...")
reader = easyocr.Reader(['en'], gpu=False, verbose=False)

img_path = "./output/rack.png"
if not os.path.exists(img_path):
    print(f"❌ 错误: 找不到文件 {img_path}")
    exit()

img = cv2.imread(img_path)
h, w, _ = img.shape
print(f"🖼️ 图片尺寸: {w}x{h}")

# ==========================================
# 2. 几何切分 (逻辑不变)
# ==========================================
NUM_TILES = 7
tile_width = w // NUM_TILES

print(f"🔪 正在切分并识别...")

results = []

for i in range(NUM_TILES):
    # 算出坐标
    x_start = i * tile_width
    x_end = (i + 1) * tile_width
    
    # Padding: 往里缩 8px，切掉圆角和阴影，只留字母
    pad_x = 8
    pad_y = 5
    roi = img[pad_y : h-pad_y, x_start+pad_x : x_end-pad_x]
    
    # 调试：保存小图，万一不对可以看一眼
    # cv2.imwrite(f"./output/debug_tile_{i}.png", roi)
    
    # ==========================================
    # 3. 识别 (EasyOCR 独门秘籍)
    # ==========================================
    try:
        # detail=0: 只返回文本字符串
        # allowlist: 【神技】强制只识别大写字母，彻底杜绝把 'I' 认成 '1' 或 '|'
        text_list = reader.readtext(
            roi, 
            detail=0, 
            allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        )
        
        # EasyOCR 可能返回空列表，或者多个结果
        if text_list:
            char = text_list[0]
        else:
            char = "_"
            
    except Exception as e:
        print(f"识别错误: {e}")
        char = "_"

    print(f"Tile {i}: [{char}]")
    results.append(char)

print("-" * 30)
print(f"🎉 最终结果: {' '.join(results)}")