import os
import uuid
import cv2
import numpy as np
import base64
from flask import Blueprint, request, jsonify, render_template
from ultralytics import YOLO

bp = Blueprint("tool_1", __name__)
UPLOAD_FOLDER = "/home/bbdwz/projects/website/uploads/vision"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 初始化 YOLOv8 Nano 模型
# 注意：在树莓派首次运行时会下载 yolov8n.pt
model = YOLO('yolov8n.pt')

# 内存缓存结构: { "image_id": { "path": str, "yolo_run": bool, "yolo_boxes": list } }
IMAGE_CACHE = {}

def encode_image_to_base64(img_array):
    """将 OpenCV 图像矩阵转换为 Base64 字符串供前端显示"""
    _, buffer = cv2.imencode('.jpg', img_array)
    return base64.b64encode(buffer).decode('utf-8')

def cleanup_old_files(folder, max_size_mb=100):
    """清理旧文件，保持目录大小在指定限制内"""
    import time
    try:
        max_size_bytes = max_size_mb * 1024 * 1024
        files = []
        total_size = 0
        
        # 遍历目录获取文件信息
        for filename in os.listdir(folder):
            filepath = os.path.join(folder, filename)
            if os.path.isfile(filepath):
                size = os.path.getsize(filepath)
                mtime = os.path.getmtime(filepath)
                files.append({
                    "path": filepath,
                    "size": size,
                    "mtime": mtime,
                    "id": filename.split('.')[0] # 用于清理内存缓存
                })
                total_size += size
        
        # 如果超出限制，按修改时间排序并删除旧文件
        if total_size > max_size_bytes:
            # 排序：最早的在前
            files.sort(key=lambda x: x["mtime"])
            
            for f in files:
                if total_size <= max_size_bytes:
                    break
                try:
                    os.remove(f["path"])
                    total_size -= f["size"]
                    # 同时清理内存缓存，防止内存泄漏
                    if f["id"] in IMAGE_CACHE:
                        del IMAGE_CACHE[f["id"]]
                    # print(f"Cleaned up old file: {f['path']}")
                except Exception as e:
                    print(f"Error removing file {f['path']}: {e}")
    except Exception as e:
        print(f"Cleanup error: {e}")

@bp.route('/api/vision/upload', methods=['POST'])
def upload_image():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    # 每次上传前执行一次目录清理，确保不超出 100MB
    cleanup_old_files(UPLOAD_FOLDER, max_size_mb=100)
    
    file = request.files['file']
    img_id = str(uuid.uuid4())
    filepath = os.path.join(UPLOAD_FOLDER, f"{img_id}.jpg")
    file.save(filepath)
    
    # 初始化该图片的缓存
    IMAGE_CACHE[img_id] = {
        "path": filepath,
        "yolo_run": False,
        "yolo_boxes": [] # 保存检测框: [class_id, x1, y1, x2, y2]
    }
    
    return jsonify({"image_id": img_id})

@bp.route('/api/vision/analyze', methods=['POST'])
def analyze_image():
    try:
        data = request.json
        img_id = data.get('image_id')
        target = data.get('target') # 'people', 'cars', 'circles'
        
        if img_id not in IMAGE_CACHE:
            return jsonify({"error": "Image not found or expired"}), 404
            
        cache = IMAGE_CACHE[img_id]
        img = cv2.imread(cache["path"])
        if img is None:
            return jsonify({"error": "Could not read image file"}), 500
        
        # 性能优化：对于 AI 检测，我们保留更高的分辨率以识别远处的小目标
        # 对于传统 CV (circles)，我们则保持较小的分辨率以提升处理速度
        if target in ['people', 'cars']:
            max_dim = 1920 # 提升到 Full HD 级别，帮助 YOLO 识别远端小目标
        else:
            max_dim = 1000 # 传统 CV 保持原样，防止形态学运算过慢
            
        h, w = img.shape[:2]
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
            
        count = 0
        debug_images = {} # 用于存储中间步骤的 Base64
        
        # ---------------- 1. 基于 YOLO 的目标检测 (人 / 车) ----------------
        if target in ['people', 'cars']:
            target_class_id = 0 if target == 'people' else 2  # YOLO 类别字典: 0=person, 2=car
            
            # 推理：增加 imgsz 参数明确检测分辨率，增加 conf 阈值微调
            results = model(img, verbose=False, imgsz=max_dim, conf=0.20)[0]
            for box in results.boxes:
                cls_id = int(box.cls[0].item())
                if cls_id == target_class_id:
                    count += 1
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    color = (0, 255, 0) if target == 'people' else (255, 0, 0)
                    label = "Person" if target == 'people' else "Car"
                    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(img, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                    
        # ---------------- 2. 彻底换思路：Canny 边缘检测 + 连通域/轮廓检测 ----------------
        elif target == 'circles':
            max_dim_cv = 1000
            h, w = img.shape[:2]
            if max(h, w) > max_dim_cv:
                scale = max_dim_cv / max(h, w)
                img = cv2.resize(img, (int(w * scale), int(h * scale)))

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # 1. 强力中值滤波：平滑木纹，保留边缘
            blurred = cv2.medianBlur(gray, 5)
            
            # 2. Canny 边缘检测：生成线稿
            edges = cv2.Canny(blurred, 40, 120)
            debug_images['1_Canny素描线稿'] = encode_image_to_base64(edges)

            def get_debug_count_img(process_img, original_img):
                """内部辅助函数：根据处理后的二值图进行检测并返回绘制结果"""
                cnts, _ = cv2.findContours(process_img, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
                pts = []
                for c in cnts:
                    a = cv2.contourArea(c)
                    if a < 40 or a > 2000: continue
                    p = cv2.arcLength(c, True)
                    if p == 0: continue
                    circ = 4 * np.pi * a / (p * p)
                    _, _, w_b, h_b = cv2.boundingRect(c)
                    r_b = min(w_b, h_b) / max(w_b, h_b)
                    if circ < 0.35 or r_b < 0.45: continue
                    (curr_x, curr_y), curr_r = cv2.minEnclosingCircle(c)
                    if curr_r < 3 or curr_r > 40: continue
                    pts.append((int(curr_x), int(curr_y), int(curr_r)))
                
                # 绘制
                res_img = original_img.copy()
                for px, py, pr in pts:
                    cv2.circle(res_img, (px, py), pr, (0, 255, 0), 1)
                    cv2.circle(res_img, (px, py), 1, (0, 0, 255), -1)
                cv2.putText(res_img, f"Count: {len(pts)}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                return res_img

            # 3. 形态学处理与同步计数对比
            # 3.1 尺寸对比: 3, 5, 7, 9
            for k_size in [3, 5, 7, 9]:
                k = np.ones((k_size, k_size), np.uint8)
                dilated_test = cv2.dilate(edges, k, iterations=1)
                debug_images[f'Dilation_Size_{k_size}x{k_size}'] = encode_image_to_base64(dilated_test)
                # 同步生成计数图
                count_img = get_debug_count_img(dilated_test, img)
                debug_images[f'Result_Size_{k_size}x{k_size}'] = encode_image_to_base64(count_img)
            
            # 3.2 迭代次数对比: 3x3 内核执行 1-5 次
            kernel_3x3 = np.ones((3, 3), np.uint8)
            for i in range(1, 6):
                dilated_iter = cv2.dilate(edges, kernel_3x3, iterations=i)
                debug_images[f'Dilation_3x3_Iter_{i}'] = encode_image_to_base64(dilated_iter)
                # 同步生成计数图
                count_img_iter = get_debug_count_img(dilated_iter, img)
                debug_images[f'Result_3x3_Iter_{i}'] = encode_image_to_base64(count_img_iter)

            # 3.3 拓扑保持膨胀 (镂空中心保护)
            # 对边缘取反，计算距离变换
            inverted_edges = cv2.bitwise_not(edges)
            dist_transform = cv2.distanceTransform(inverted_edges, cv2.DIST_L2, 5)
            # 提取局部最大值作为种子 (保护黑洞中心)
            _, peaks = cv2.threshold(dist_transform, 0.4 * dist_transform.max(), 255, cv2.THRESH_BINARY)
            peaks = peaks.astype(np.uint8)
            debug_images['Topo_Seeds(中心保护点)'] = encode_image_to_base64(peaks)

            for i in [2, 4, 6]:
                # 正常膨胀
                dilated_raw = cv2.dilate(edges, kernel_3x3, iterations=i)
                # 强行挖掉中心点，保持拓扑结构 (不消灭空间)
                topo_dilated = cv2.bitwise_and(dilated_raw, cv2.bitwise_not(peaks))
                debug_images[f'Topo_Dilation_Iter_{i}'] = encode_image_to_base64(topo_dilated)
                # 同步计数
                topo_count_img = get_debug_count_img(topo_dilated, img)
                debug_images[f'Topo_Result_Iter_{i}'] = encode_image_to_base64(topo_count_img)
                
                # 如果是 Iter 6，将其定为最终返回的主结果
                if i == 6:
                    # 重新运行一次完整的计数逻辑以获取 valid_points 和最终叠加图
                    cnts_final, _ = cv2.findContours(topo_dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
                    valid_points = []
                    for c_f in cnts_final:
                        a_f = cv2.contourArea(c_f)
                        if a_f < 40 or a_f > 2000: continue
                        p_f = cv2.arcLength(c_f, True)
                        if p_f == 0: continue
                        circ_f = 4 * np.pi * a_f / (p_f * p_f)
                        _, _, w_f, h_f = cv2.boundingRect(c_f)
                        r_f = min(w_f, h_f) / max(w_f, h_f)
                        if circ_f < 0.35 or r_f < 0.45: continue
                        (cX_f, cY_f), radius_f = cv2.minEnclosingCircle(c_f)
                        if radius_f < 3 or radius_f > 40: continue
                        valid_points.append((int(cX_f), int(cY_f), int(radius_f)))
                    
                    count = len(valid_points)
                    # 绘制主界面显示的最终叠加效果
                    overlay = img.copy()
                    for cX_p, cY_p, r_p in valid_points:
                        cv2.circle(overlay, (cX_p, cY_p), r_p, (100, 255, 100), -1)
                    
                    alpha = 0.4
                    img = cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0)
                    for cX_p, cY_p, r_p in valid_points:
                        cv2.circle(img, (cX_p, cY_p), r_p, (0, 255, 0), 1)
                        cv2.circle(img, (cX_p, cY_p), 1, (0, 0, 255), -1)

            # 最终决定用于检测的参数内核（用于保留结构）
            kernel = np.ones((3, 3), np.uint8)
            # 这里的 closed 仅作为 debug 存档，主逻辑已切换到上面的 Topo 流程
            closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
            debug_images['3_对比参考图(3x3_iter2)'] = encode_image_to_base64(closed)

        else:
            return jsonify({"error": "Unknown target"}), 400

        # 返回加入了 debug_images 字典的数据
        return jsonify({
            "count": count,
            "image_base64": f"data:image/jpeg;base64,{encode_image_to_base64(img)}",
            "debug_images": debug_images
        })
    except Exception as e:
        import traceback
        print(f"Vision analysis error: {traceback.format_exc()}")
        return jsonify({"error": f"Internal process error: {str(e)}"}), 500
