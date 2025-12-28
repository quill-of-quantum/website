import cv2
import numpy as np
import easyocr
import os

# 1. 初始化
reader = easyocr.Reader(['en'], gpu=False, verbose=False)

img_path = "./output/rack.png"
if not os.path.exists(img_path):
    print("❌ 找不到图片")
    exit()

img = cv2.imread(img_path)
h, w, _ = img.shape
print(f"🖼️ 原始尺寸: {w}x{h}")

# ==========================================================
# 阶段一：定位方块 (保持你现在的成功逻辑)
# ==========================================================
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
enhanced_gray = clahe.apply(gray)
binary_map = cv2.adaptiveThreshold(enhanced_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
kernel = np.ones((3,3), np.uint8)
binary_map = cv2.erode(binary_map, kernel, iterations=1)
cnts, _ = cv2.findContours(binary_map, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

valid_boxes = []
for c in cnts:
    x, y, cw, ch = cv2.boundingRect(c)
    if 30 < cw < 150 and 30 < ch < 150:
        ratio = cw / float(ch)
        if 0.8 < ratio < 1.2:
            valid_boxes.append((x, y, cw, ch))

valid_boxes = sorted(valid_boxes, key=lambda b: b[0])

# 去重逻辑
final_boxes = []
for box in valid_boxes:
    if not final_boxes:
        final_boxes.append(box)
    else:
        last_box = final_boxes[-1]
        if abs(box[0] - last_box[0]) < 20:
            if (box[2]*box[3]) > (last_box[2]*last_box[3]):
                final_boxes.pop()
                final_boxes.append(box)
        else:
            final_boxes.append(box)

print(f"✅ 定位成功: {len(final_boxes)} 个方块")

# ==========================================================
# 阶段二：二次切割 (Sub-cropping)
# ==========================================================
results = []
debug_img = img.copy()

for i, (x, y, cw, ch) in enumerate(final_boxes):
    # 1. 获取整个方块 ROI
    # padding 稍微小一点，保证不切掉边缘
    pad = 2
    tile_roi = img[y+pad : y+ch-pad, x+pad : x+cw-pad]
    
    # 获取切出来的方块尺寸
    th, tw, _ = tile_roi.shape
    
    # ----------------------------------------------------
    # ✂️ 核心修改：只切出中间的字母区域
    # ----------------------------------------------------
    # 逻辑：
    # 字母通常在中心，稍微偏下一点。
    # 数字在右上角，我们把右上角切掉，或者只保留中心区域。
    
    # 方案：保留中心 60% 的区域，丢弃四周（包括右上角的数字）
    # y1: 上面切掉 25% (避开数字)
    # y2: 下面切掉 15%
    # x1: 左边切掉 15%
    # x2: 右边切掉 25% (避开数字)
    
    crop_y1 = int(th * 0.25)
    crop_y2 = int(th * 0.85)
    crop_x1 = int(tw * 0.15)
    crop_x2 = int(tw * 0.75) # 右边多切点，因为数字在右边
    
    letter_roi = tile_roi[crop_y1:crop_y2, crop_x1:crop_x2]
    
    # 2. 图像增强 (放大 + 灰度)
    # 放大 3 倍，让字母特征更明显
    roi_zoom = cv2.resize(letter_roi, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    roi_gray = cv2.cvtColor(roi_zoom, cv2.COLOR_BGR2GRAY)
    
    # 可选：二值化 (让字母变黑，背景变白，或者反过来)
    # EasyOCR 有时候喜欢黑底白字，有时候喜欢白底黑字。
    # 对于这个游戏字体（粗体圆角），Otsu 二值化通常效果不错
    _, roi_binary = cv2.threshold(roi_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    # 保存这一步的图片，这是关键！
    # 你应该只看到一个大大的字母，没有角落的数字
    cv2.imwrite(f"./output/clean_letter_{i}.png", roi_binary)

    # 3. 识别
    char = "_"
    try:
        # allowlist 只允许大写字母
        # 使用 roi_binary 或者 roi_gray 都可以尝试
        res = reader.readtext(roi_binary, detail=0, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ')
        
        # 结果过滤
        if res:
            # 取最像的
            char = res[0]
            
            # 常见误识别修正 (针对这种圆角字体)
            if char == 'I' and 'L' in char: char = 'L' 
            
    except Exception as e:
        pass

    # 在原图画框展示
    cv2.rectangle(debug_img, (x, y), (x+cw, y+ch), (0, 255, 0), 2)
    # 把识别结果写在图上
    cv2.putText(debug_img, char, (x, y-5), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    
    print(f"Tile {i}: [{char}]")
    results.append(char)

cv2.imwrite("./output/final_result_view.png", debug_img)
print("-" * 30)
print(f"🎉 最终结果: {' '.join(results)}")