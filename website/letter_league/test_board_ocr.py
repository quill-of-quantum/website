import cv2
import numpy as np
import easyocr
import os
import shutil

# 1. 初始化
reader = easyocr.Reader(['en'], gpu=False, verbose=False)

img_path = "./output/board.png"
if not os.path.exists(img_path):
    print("❌ 找不到图片")
    exit()

img = cv2.imread(img_path)
h, w, _ = img.shape

output_dir = "./output/board_final_opt"
if os.path.exists(output_dir):
    shutil.rmtree(output_dir)
os.makedirs(output_dir)

# ==========================================================
# 步骤 1: 定位 (还是用最稳的"黑字提取法")
# ==========================================================
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
# 提取黑色墨水 (背景是白的，字是黑的 -> 反转后字是白的)
_, binary_finder = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)
# 稍微连通一下，保证能找到完整的字块
kernel_finder = np.ones((3,3), np.uint8)
binary_finder = cv2.dilate(binary_finder, kernel_finder, iterations=2)

cnts, _ = cv2.findContours(binary_finder, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
print(f"🔍 定位到 {len(cnts)} 个目标")

final_tiles = []
for c in cnts:
    x, y, cw, ch = cv2.boundingRect(c)
    # 尺寸筛选
    if not (10 < cw < 150 and 10 < ch < 150): continue
    # 形状筛选
    ratio = cw / float(ch)
    if not (0.2 < ratio < 2.0): continue
    final_tiles.append((x, y, cw, ch))

# 排序
final_tiles = sorted(final_tiles, key=lambda b: (b[1], b[0]))
print(f"✅ 锁定 {len(final_tiles)} 个字母块")

# ==========================================================
# 步骤 2: 增强识别 (瘦身 + 原色)
# ==========================================================
results = []
debug_img = img.copy()

# 易混淆字符修正表 (针对粗体字)
correction_map = {
    '0': 'O', '8': 'B', '6': 'G', '5': 'S', '1': 'I', '2': 'Z'
}

for i, (x, y, cw, ch) in enumerate(final_tiles):
    # 1. 切出包含字母的方块 (从原图切，保留颜色细节)
    center_x = x + cw // 2
    center_y = y + ch // 2
    size = max(cw, ch) + 16 # 多留点边距
    half = size // 2
    
    y1 = max(0, center_y - half)
    y2 = min(h, center_y + half)
    x1 = max(0, center_x - half)
    x2 = min(w, center_x + half)
    
    roi_color = img[y1:y2, x1:x2]
    roi_gray = cv2.cvtColor(roi_color, cv2.COLOR_BGR2GRAY)
    
    # ---------------------------------------------------------
    # 方案 A: "瘦身"二值化 (Eroded Binary) - 专治 B 和 O
    # ---------------------------------------------------------
    # 先做二值化 (黑底白字)
    _, roi_binary = cv2.threshold(roi_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    
    # 【关键操作】腐蚀 (Erode)
    # 就像拿砂纸把字的边缘磨掉一层，让中间的洞变大
    kernel_thin = np.ones((2,2), np.uint8) # 2x2 的核，轻微腐蚀
    roi_thin = cv2.erode(roi_binary, kernel_thin, iterations=1)
    
    # 加边框防止贴边
    roi_thin = cv2.copyMakeBorder(roi_thin, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=(0,0,0))
    cv2.imwrite(f"{output_dir}/tile_{i}_thin.png", roi_thin)

    # ---------------------------------------------------------
    # 方案 B: 高清反转灰度 (Inverted Grayscale) - 保留细节
    # ---------------------------------------------------------
    # 很多时候二值化会把细节搞丢，灰度图保留了抗锯齿信息
    # EasyOCR 喜欢白底黑字，或者黑底白字，这里我们反转一下让字变亮
    roi_inverted_gray = cv2.bitwise_not(roi_gray)
    
    # 稍微拉高对比度 (直方图均衡化)
    roi_inverted_gray = cv2.equalizeHist(roi_inverted_gray)
    
    # 加边框
    roi_inverted_gray = cv2.copyMakeBorder(roi_inverted_gray, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=(0,0,0))
    cv2.imwrite(f"{output_dir}/tile_{i}_gray.png", roi_inverted_gray)
    
    # ---------------------------------------------------------
    # 3. 混合识别逻辑
    # ---------------------------------------------------------
    char = ""
    # 允许数字，方便我们把 8 救回来变成 B
    safe_allowlist = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    
    try:
        # 优先尝试【瘦身版】，因为它对 B/O 最有效
        res = reader.readtext(roi_thin, detail=0, allowlist=safe_allowlist)
        
        # 如果瘦身版没认出来，或者置信度低，尝试【灰度版】
        if not res:
            res = reader.readtext(roi_inverted_gray, detail=0, allowlist=safe_allowlist)
            
        if res:
            raw_char = res[0].upper()
            
            # --- 字符修正 (Mapping) ---
            # 如果识别出 8，大概率是 B；如果识别出 0，大概率是 O
            if raw_char in correction_map:
                char = correction_map[raw_char]
            # 过滤掉多余字符 (比如 "D2" -> "D")
            elif len(raw_char) > 1 and raw_char[0].isalpha():
                char = raw_char[0]
            elif raw_char.isalpha():
                char = raw_char
                
    except Exception as e:
        print(f"Error tile {i}: {e}")

    # ---------------------------------------------------------
    # 4. 结果绘制
    # ---------------------------------------------------------
    if char:
        # 还原坐标画框
        cv2.rectangle(debug_img, (x1, y1), (x2, y2), (0, 255, 0), 2)
        # 字体加粗一点，用黄色显示，显眼
        cv2.putText(debug_img, char, (x1, y1-5), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
        results.append(char)
        print(f"Tile {i}: {char}")
    else:
        cv2.rectangle(debug_img, (x1, y1), (x2, y2), (0, 0, 255), 2)

# 保存最终结果
cv2.imwrite("./output/board_final_thin.png", debug_img)
print("-" * 30)
print(f"🎉 识别结果: {' '.join(results)}")