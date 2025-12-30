from flask import Blueprint, render_template, request, jsonify, Response
import json
import base64
import io
from PIL import Image
import cv2
import numpy as np
import easyocr
import os
import math
import time
from collections import defaultdict

bp = Blueprint("letter", __name__)

# 获取当前文件所在的目录绝对路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==============================================================================
# ⚙️ 用户配置区域 (USER CONFIGURATION)
# ==============================================================================

INPUT_IMAGE = os.path.join(BASE_DIR, "test.png")
LOGO_IMAGE  = os.path.join(BASE_DIR, "logo.png")
DICT_FILE   = os.path.join(BASE_DIR, "twl06_ENABLE.txt")
OUTPUT_DIR  = os.path.join(BASE_DIR, "output")

REC_TOP_N   = 3    # 1. 最佳长词推荐数
REC_SHORT_N = 3    # 2. 短词防守推荐数
REC_MULTI_N = 3    # 3. 【新增】多重组词推荐数 (一箭多雕)

MIN_DIST    = 3    # 走法间距
SHORT_LEN   = 4    # 短词定义

VIS_SHOW_DEBUG = True # Enable debug for web context

# ==============================================================================
# 🧠 第一部分：GADDAG 核心算法
# ==============================================================================

class GADDAGNode:
    __slots__ = ['edges', 'is_end']
    def __init__(self):
        self.edges = {} 
        self.is_end = False

class GADDAG:
    def __init__(self, dict_path=None):
        self.root = GADDAGNode()
        self.delimiter = '>'
        words = []
        if dict_path and os.path.exists(dict_path):
            print(f"📖 正在加载字典: {dict_path} ...")
            with open(dict_path, 'r') as f:
                for line in f:
                    w = line.strip().upper()
                    if len(w) > 1: words.append(w)
        else:
            print("⚠️ 使用内置微型字典...")
            words = ["APPLE", "BANANA", "CAT", "DOG", "HELLO", "WORLD", "TEST", "LETTER", "LEAGUE", "CODE", "DATA", "GAME", "BOARD", "RACK", "FROM", "FORM", "FA"]
        for w in words:
            self.add_word(w)
            
    def add_word(self, word):
        n = len(word)
        for i in range(1, n + 1):
            prefix = word[:i]
            suffix = word[i:]
            path = prefix[::-1] + self.delimiter + suffix
            self._insert(path)
        self._insert(word[::-1] + self.delimiter)

    def _insert(self, path):
        node = self.root
        for char in path:
            if char not in node.edges:
                node.edges[char] = GADDAGNode()
            node = node.edges[char]
        node.is_end = True
    
    def contains(self, word):
        path = word.upper()[::-1] + self.delimiter
        node = self.root
        for c in path:
            if c not in node.edges: return False
            node = node.edges[c]
        return node.is_end

# ==============================================================================
# 🧠 第二部分：求解器 (增加交叉词计数逻辑)
# ==============================================================================

class ScrabbleSolver:
    def __init__(self, gaddag):
        self.g = gaddag
        self.board = []
        self.rack = []
        self.results = []
        self.rows = 15
        self.cols = 15
        self.cross_sets = []

    def set_board(self, board_matrix):
        self.board = board_matrix
        self.rows = len(board_matrix)
        self.cols = len(board_matrix[0]) if self.rows > 0 else 0
        self.cross_sets = [[set() for _ in range(self.cols)] for _ in range(self.rows)]

    def solve(self, rack_str):
        self.rack = list(rack_str.upper())
        self.results = []
        self._compute_cross_sets()
        for row in range(self.rows):
            self._gen_row(row)
            
        unique = {}
        for res in self.results:
            key = f"{res['word']}_{res['row']}_{res['col']}"
            if key not in unique: unique[key] = res
        
        # 默认按长度排序，但保留了 'cross' 字段供后续筛选
        return sorted(unique.values(), key=lambda x: len(x['word']), reverse=True)

    def _compute_cross_sets(self):
        full_set = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        for r in range(self.rows):
            for c in range(self.cols):
                if self.board[r][c] == '':
                    self.cross_sets[r][c] = full_set.copy()
                else:
                    self.cross_sets[r][c] = set()
        
        for c in range(self.cols):
            for r in range(self.rows):
                if self.board[r][c] == '':
                    top = (r > 0 and self.board[r-1][c] != '')
                    bottom = (r < self.rows-1 and self.board[r+1][c] != '')
                    if top or bottom:
                        valid = set()
                        start = r
                        while start > 0 and self.board[start-1][c] != '': start -= 1
                        end = r
                        while end < self.rows-1 and self.board[end+1][c] != '': end += 1
                        
                        prefix = "".join([self.board[k][c] for k in range(start, r)])
                        suffix = "".join([self.board[k][c] for k in range(r+1, end+1)])
                        
                        for char in full_set:
                            candidate = prefix + char + suffix
                            if self.g.contains(candidate):
                                valid.add(char)
                        self.cross_sets[r][c] = valid

    # 【新增】辅助函数：计算放置这个词会形成几个额外的纵向单词
    def _count_cross_words(self, word, row, col):
        cross_count = 0
        for i, char in enumerate(word):
            c = col + i
            # 只检查原本是空格的位置（即我们新放牌的位置）
            if 0 <= c < self.cols and self.board[row][c] == '':
                # 检查上下是否有邻居
                has_top = (row > 0 and self.board[row-1][c] != '')
                has_bottom = (row < self.rows-1 and self.board[row+1][c] != '')
                if has_top or has_bottom:
                    cross_count += 1
        return cross_count

    def _gen_row(self, row):
        line = self.board[row]
        anchors = []
        for i in range(self.cols):
            if line[i] == '':
                if (i>0 and line[i-1]!='') or (i<self.cols-1 and line[i+1]!='') or \
                   (row>0 and self.board[row-1][i]!='') or (row<self.rows-1 and self.board[row+1][i]!=''):
                    anchors.append(i)
        
        if not anchors and all(c=='' for r_ in self.board for c in r_):
            anchors.append(self.cols // 2)

        for anchor in anchors:
            if anchor > 0 and line[anchor-1] != '': continue
            
            allowed = self.cross_sets[row][anchor]
            unique_rack = set(self.rack)
            # 【修复】只在实际有?时才将allowed设为全集
            candidates = allowed.intersection(unique_rack)
            # 如果手牌里有?，则可以尝试所有allowed中的字母
            if '?' in self.rack:
                candidates = candidates.union(allowed)

            for char in candidates:
                if char in self.g.root.edges:
                    # 【修复】优先使用实际字母，只在没有时才用?
                    to_remove = char if char in self.rack else ('?' if '?' in self.rack else None)
                    if to_remove and to_remove in self.rack:
                        self.rack.remove(to_remove)
                        display_char = char.lower() if to_remove == '?' else char
                        new_node = self.g.root.edges[char]
                        self._gen(row, anchor-1, display_char, new_node, anchor, "LEFT", tiles_placed=1)
                        if self.g.delimiter in new_node.edges:
                            right_node = new_node.edges[self.g.delimiter]
                            self._gen(row, anchor+1, display_char, right_node, anchor, "RIGHT", tiles_placed=1)
                        self.rack.append(to_remove)

    def _gen(self, row, col, word, node, anchor_pos, direction, tiles_placed):
        if direction == "LEFT":
            if self.g.delimiter in node.edges:
                right_node = node.edges[self.g.delimiter]
                self._gen(row, anchor_pos + 1, word, right_node, anchor_pos, "RIGHT", tiles_placed)
            
            if col >= 0:
                char_on_board = self.board[row][col]
                if char_on_board != '': 
                    if char_on_board in node.edges:
                        self._gen(row, col - 1, char_on_board + word, node.edges[char_on_board], anchor_pos, "LEFT", tiles_placed)
                else: 
                    allowed = self.cross_sets[row][col]
                    unique_rack = set(self.rack)
                    # 【修复】只在实际有?时才将allowed设为全集
                    candidates = allowed.intersection(unique_rack)
                    if '?' in self.rack:
                        candidates = candidates.union(allowed)

                    for char in candidates:
                        if char in node.edges:
                            # 【修复】优先使用实际字母，只在没有时才用?
                            to_remove = char if char in self.rack else ('?' if '?' in self.rack else None)
                            if to_remove and to_remove in self.rack:
                                self.rack.remove(to_remove)
                                display_char = char.lower() if to_remove == '?' else char
                                self._gen(row, col - 1, display_char + word, node.edges[char], anchor_pos, "LEFT", tiles_placed + 1)
                                self.rack.append(to_remove)

        elif direction == "RIGHT":
            if node.is_end:
                if (col >= self.cols or self.board[row][col] == '') and tiles_placed > 0:
                    start_col = col - len(word)
                    if start_col >= 0 and col <= self.cols:
                         # 【核心】计算形成了多少个交叉词 (Cross Words)
                         cross_cnt = self._count_cross_words(word, row, start_col)
                         self.results.append({
                             'word': word, 
                             'row': row, 
                             'col': start_col,
                             'cross': cross_cnt # 存入结果
                         })

            if col < self.cols:
                char_on_board = self.board[row][col]
                if char_on_board != '':
                    if char_on_board in node.edges:
                        self._gen(row, col + 1, word + char_on_board, node.edges[char_on_board], anchor_pos, "RIGHT", tiles_placed)
                else:
                    allowed = self.cross_sets[row][col]
                    unique_rack = set(self.rack)
                    # 【修复】只在实际有?时才将allowed设为全集
                    candidates = allowed.intersection(unique_rack)
                    if '?' in self.rack:
                        candidates = candidates.union(allowed)
                        
                    for char in candidates:
                        if char in node.edges:
                            # 【修复】优先使用实际字母，只在没有时才用?
                            to_remove = char if char in self.rack else ('?' if '?' in self.rack else None)
                            if to_remove and to_remove in self.rack:
                                self.rack.remove(to_remove)
                                display_char = char.lower() if to_remove == '?' else char
                                self._gen(row, col + 1, word + display_char, node.edges[char], anchor_pos, "RIGHT", tiles_placed + 1)
                                self.rack.append(to_remove)

# ==============================================================================
# 👁️ 第三部分：可视化 (颜色分组)
# ==============================================================================

class LetterLeagueVision:
    def __init__(self):
        print("👁️ 初始化视觉模块...")
        self.reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        self.out_dir = OUTPUT_DIR
        os.makedirs(self.out_dir, exist_ok=True)
        self.correction_map = {'0': 'O', '8': 'B', '6': 'G', '5': 'S', '1': 'I', '2': 'Z'}
        self.grid_params = None 
        self.seg_board_img = None 
        self.debug_info = {} # Store debug data

    def process_full_pipeline(self, img_input, logo_path):
        # Modified to accept numpy array or path
        if isinstance(img_input, str):
            img = cv2.imread(img_input)
        else:
            img = img_input
            
        if img is None: raise FileNotFoundError(f"无法读取图片")
        board_img, rack_img = self.segment_image(img, logo_path)
        self.seg_board_img = board_img 
        
        # Capture segmentation debug images
        if board_img is not None and board_img.size > 0:
            self.debug_info['seg_board'] = self._img_to_base64(board_img)
        if rack_img is not None and rack_img.size > 0:
            self.debug_info['seg_rack'] = self._img_to_base64(rack_img)

        rack_letters = []
        if rack_img is not None and rack_img.size > 0:
            rack_letters = self.ocr_rack(rack_img)
            self.debug_info['ocr_rack_result'] = rack_letters
            
        board_matrix = [['' for _ in range(15)] for _ in range(15)]
        if board_img is not None and board_img.size > 0:
            board_matrix = self.ocr_board(board_img)
            self.debug_info['ocr_board_matrix'] = board_matrix
            
        return board_matrix, rack_letters

    def _img_to_base64(self, img):
        # Handle both color and grayscale images
        if len(img.shape) == 2:  # Grayscale image
            img_bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        else:
            img_bgr = img
        _, buffer = cv2.imencode('.png', img_bgr)
        return f"data:image/png;base64,{base64.b64encode(buffer).decode('utf-8')}"

    def segment_image(self, img, logo_path):
        h_img, w_img = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        lx, ly, lh, lw = 0, 0, 0, 0
        found_logo = False
        maxv = 0.0 # Initialize maxv
        
        # Debug: Check logo path
        logo_exists = os.path.exists(logo_path)
        
        if logo_exists:
            tpl_l = cv2.imread(logo_path, 0)
            if tpl_l is not None:
                res = cv2.matchTemplate(gray, tpl_l, cv2.TM_CCOEFF_NORMED)
                _, maxv, _, maxloc = cv2.minMaxLoc(res)
                if maxv > 0.4:  # 从0.7降低到0.4，更容易检测到logo
                    lx, ly = maxloc
                    lh, lw = tpl_l.shape
                    found_logo = True
                    print(f"⚓ Logo 匹配成功 (分值: {maxv:.2f})")
        
        # Store debug info
        self.debug_info['logo_detection'] = {
            'found': found_logo,
            'score': float(maxv),
            'path': logo_path,
            'exists': logo_exists
        }

        if not found_logo:
            lx, ly, lh, lw = 0, 0, int(h_img*0.05), int(w_img*0.05)
        game_left = lx
        game_width = w_img - game_left
        game_top = int(ly + lh * 0.10)
        game_height = int(game_width * 0.5)
        game_bottom = min(h_img, game_top + game_height)
        game_w = game_width
        game_h = game_height
        def safe_crop(x0, y0, w0, h0):
            x = int(game_left + x0 * game_w)
            y = int(game_top  + y0 * game_h)
            w = int(w0 * game_w)
            h = int(h0 * game_h)
            x1, x2 = max(0, x), min(w_img, x+w)
            y1, y2 = max(0, y), min(h_img, y+h)
            if x2 <= x1 or y2 <= y1: return np.array([])
            return img[y1:y2, x1:x2]
        board = safe_crop(0.00, 0.13, 0.9, 0.70)
        # 调整rack截取位置的4个参数: (x_offset, y_offset, width, height)
        # 当前: x=0.35(35%右移), y=0.87(87%下移), w=0.28(28%宽度), h=0.11(11%高度)
        rack  = safe_crop(0.30, 0.85, 0.35, 0.12)  # 示例调整
        # Removed file writing for web context, handled in process_full_pipeline
        return board, rack

    def ocr_rack(self, img):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced_gray = clahe.apply(gray)
        binary_map = cv2.adaptiveThreshold(enhanced_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        binary_map = cv2.erode(binary_map, np.ones((3,3), np.uint8), iterations=1)
        
        # === 统一：保存这个二值化图像，轮廓检测也用这个 ===
        self.debug_info['rack_binary_map'] = self._img_to_base64(binary_map)
        
        # 直接在这个 binary_map 上进行轮廓检测
        cnts, _ = cv2.findContours(binary_map, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # 生成轮廓检测调试图像 - 叠加到原图上
        rack_contour_debug = img.copy()
        
        # === 同时生成轮廓叠加到二值化图像上的版本 ===
        binary_contour_debug = cv2.cvtColor(binary_map, cv2.COLOR_GRAY2BGR)
        
        valid_boxes = []
        rejected_boxes = []
        
        for i, c in enumerate(cnts):
            x, y, cw, ch = cv2.boundingRect(c)
            # 使用test.py中的更严格过滤条件
            area = cw * ch
            ratio = cw / float(ch)
            
            if (30 < cw < 150 and 30 < ch < 150 and 
                0.8 < ratio < 1.2):  # 更严格的正方形比例
                valid_boxes.append((x, y, cw, ch))
                # 绘制绿色框表示接受 - 同时画到两个图上
                cv2.rectangle(rack_contour_debug, (x, y), (x+cw, y+ch), (0, 255, 0), 2)
                cv2.rectangle(binary_contour_debug, (x, y), (x+cw, y+ch), (0, 255, 0), 2)
                cv2.putText(rack_contour_debug, f"OK{len(valid_boxes)}", (x, y-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                cv2.putText(binary_contour_debug, f"OK{len(valid_boxes)}", (x, y-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
            else:
                # 绘制红色框表示拒绝 - 同时画到两个图上
                cv2.rectangle(rack_contour_debug, (x, y), (x+cw, y+ch), (0, 0, 255), 2)
                cv2.rectangle(binary_contour_debug, (x, y), (x+cw, y+ch), (0, 0, 255), 2)
                if not (30 < cw < 150 and 30 < ch < 150):
                    reason = f"size({cw}x{ch})"
                elif not (0.8 < ratio < 1.2):
                    reason = f"ratio({ratio:.2f})"
                else:
                    reason = "other"
                cv2.putText(rack_contour_debug, f"X{reason}", (x, y-5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
                cv2.putText(binary_contour_debug, f"X{reason}", (x, y-5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
                rejected_boxes.append((x, y, cw, ch, reason))
        
        # 保存两种轮廓检测调试图像
        self.debug_info['rack_contour_debug'] = self._img_to_base64(rack_contour_debug)
        self.debug_info['rack_binary_contour_debug'] = self._img_to_base64(binary_contour_debug)
        
        # --- Y轴对齐过滤 ---
        y_filtered_boxes = []
        y_filter_reason = "no_valid_boxes"
        
        if valid_boxes:
            import statistics
            y_coords = [b[1] for b in valid_boxes]
            median_y = statistics.median(y_coords)
            y_tolerance = 20 
            
            y_filtered_boxes = []
            y_rejected_boxes = []
            
            for box in valid_boxes:
                y_deviation = abs(box[1] - median_y)
                if y_deviation < y_tolerance:
                    y_filtered_boxes.append(box)
                else:
                    y_rejected_boxes.append((box, y_deviation))
            
            y_filter_reason = f"median_y={median_y:.1f}, tolerance={y_tolerance}, " + \
                            f"kept={len(y_filtered_boxes)}, rejected={len(y_rejected_boxes)}"
            
            valid_boxes = y_filtered_boxes
        
        # --- 面积中位数过滤 ---
        area_filtered_boxes = []
        area_filter_reason = "no_valid_boxes"
        
        if valid_boxes:
            areas = [b[2] * b[3] for b in valid_boxes]
            median_area = statistics.median(areas)
            
            area_filtered_boxes = []
            area_rejected_boxes = []
            
            for box in valid_boxes:
                area = box[2] * box[3]
                area_ratio = area / median_area
                if 0.6 < area_ratio < 1.4:
                    area_filtered_boxes.append(box)
                else:
                    area_rejected_boxes.append((box, area, area_ratio))
            
            area_filter_reason = f"median_area={median_area:.0f}, range=0.6-1.4x, " + \
                               f"kept={len(area_filtered_boxes)}, rejected={len(area_rejected_boxes)}"
            
            valid_boxes = area_filtered_boxes
        
        # Store filter debug info
        self.debug_info['rack_y_filter'] = {
            'reason': y_filter_reason,
            'before_count': len(y_filtered_boxes) if y_filtered_boxes else 0,
            'after_count': len(valid_boxes)
        }
        
        self.debug_info['rack_area_filter'] = {
            'reason': area_filter_reason,
            'before_count': len(area_filtered_boxes) + len([r for r in (area_rejected_boxes if 'area_rejected_boxes' in locals() else [])]),
            'after_count': len(valid_boxes)
        }
        
        # 按x坐标排序并去重
        valid_boxes.sort(key=lambda b: b[0])
        final_boxes = []
        for box in valid_boxes:
            if not final_boxes: 
                final_boxes.append(box)
            else:
                last = final_boxes[-1]
                if abs(box[0]-last[0]) < 20:
                    if box[2]*box[3] > last[2]*last[3]: 
                        final_boxes[-1] = box
                else: 
                    final_boxes.append(box)
        
        results = []
        rack_debug_data = []
        
        # 记录轮廓检测统计
        rack_debug_data.append({
            'index': -1,
            'raw': f"总轮廓:{len(cnts)} 尺寸+比例:{len(valid_boxes)+len(rejected_boxes)} Y轴过滤后:{len(y_filtered_boxes)} 面积过滤后:{len(valid_boxes)} 最终去重:{len(final_boxes)}",
            'final': 'STATS',
            'process': f"Y轴: {y_filter_reason[:50]}... | 面积: {area_filter_reason[:50]}...",
            'steps': []  # No steps for stats row
        })
        
        # === 定义宽松的白名单：允许 OCR 把 I 识别成 1/l/|/! ===
        safe_allowlist = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ1l|!'
        
        # === 新的渐进式OCR识别流程 ===
        for i, (x, y, cw, ch) in enumerate(final_boxes):
            # 1. 基础裁切：获取完整的方块区域（含边框）
            pad = 2
            tile_roi = img[y+pad : y+ch-pad, x+pad : x+cw-pad]
            th, tw = tile_roi.shape[:2]
            if th==0 or tw==0: continue
            
            # 2. 二次裁切：聚焦字母区域
            crop_y1 = int(th * 0.20)
            crop_y2 = int(th * 0.85)
            crop_x1 = int(tw * 0.20)
            crop_x2 = int(tw * 0.80)
            
            letter_roi = tile_roi[crop_y1:crop_y2, crop_x1:crop_x2]
            if letter_roi.size == 0: continue
            
            # === 渐进式识别（早停）===
            char = ""
            raw_result = ""
            success_step = ""
            attempt_steps = []
            
            # Step 1: 原图放大识别（使用宽松白名单）
            try:
                roi_zoom = cv2.resize(letter_roi, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                res = self.reader.readtext(roi_zoom, detail=0, allowlist=safe_allowlist)
                
                # Store attempt
                attempt_steps.append({
                    'name': '原图×3',
                    'image': self._img_to_base64(roi_zoom),
                    'result': res[0].upper() if res else '(empty)',
                    'success': bool(res and res[0].strip())
                })
                
                if res and res[0].strip():
                    raw_result = res[0].upper()
                    success_step = "原图×3"
            except Exception as e:
                attempt_steps.append({
                    'name': '原图×3',
                    'image': self._img_to_base64(cv2.resize(letter_roi, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)),
                    'result': f'error:{str(e)[:10]}',
                    'success': False
                })
            
            # Step 2: 如果原图失败，尝试二值化（使用宽松白名单）
            if not raw_result:
                try:
                    roi_gray = cv2.cvtColor(letter_roi, cv2.COLOR_BGR2GRAY)
                    _, roi_binary = cv2.threshold(roi_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    roi_binary_zoom = cv2.resize(roi_binary, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                    
                    res = self.reader.readtext(roi_binary_zoom, detail=0, allowlist=safe_allowlist)
                    
                    attempt_steps.append({
                        'name': 'Otsu二值化×3',
                        'image': self._img_to_base64(roi_binary_zoom),
                        'result': res[0].upper() if res else '(empty)',
                        'success': bool(res and res[0].strip())
                    })
                    
                    if res and res[0].strip():
                        raw_result = res[0].upper()
                        success_step = "Otsu二值化×3"
                except Exception as e:
                    attempt_steps.append({
                        'name': 'Otsu二值化×3',
                        'image': '',
                        'result': f'error:{str(e)[:10]}',
                        'success': False
                    })
            
            # Step 3: 如果仍然失败，尝试腐蚀（使用宽松白名单）
            if not raw_result:
                try:
                    roi_gray = cv2.cvtColor(letter_roi, cv2.COLOR_BGR2GRAY)
                    _, roi_binary = cv2.threshold(roi_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    roi_eroded = cv2.erode(roi_binary, np.ones((4,4), np.uint8), iterations=1)
                    roi_eroded_zoom = cv2.resize(roi_eroded, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                    
                    res = self.reader.readtext(roi_eroded_zoom, detail=0, allowlist=safe_allowlist)
                    
                    attempt_steps.append({
                        'name': '腐蚀处理×3',
                        'image': self._img_to_base64(roi_eroded_zoom),
                        'result': res[0].upper() if res else '(empty)',
                        'success': bool(res and res[0].strip())
                    })
                    
                    if res and res[0].strip():
                        raw_result = res[0].upper()
                        success_step = "腐蚀处理×3"
                except Exception as e:
                    attempt_steps.append({
                        'name': '腐蚀处理×3',
                        'image': '',
                        'result': f'error:{str(e)[:10]}',
                        'success': False
                    })
            
            # === 后处理修正：将错就错，最后扶正 ===
            if raw_result:
                # 强制修正：1/l/|/! 统统认为是 I
                if raw_result in ['1', 'L', '|', '!']:  # Note: uppercase L after .upper()
                    char = 'I'
                    success_step += f" → 强制修正({raw_result}→I)"
                elif raw_result in self.correction_map: 
                    char = self.correction_map[raw_result]
                    success_step += f" → 修正({raw_result}→{char})"
                elif len(raw_result)>1 and raw_result[0].isalpha(): 
                    # Multi-char result, take first letter and check if it needs I correction
                    first_char = raw_result[0]
                    if first_char in ['1', 'L', '|', '!']:
                        char = 'I'
                        success_step += f" → 取首字母+修正({raw_result}→{first_char}→I)"
                    else:
                        char = first_char
                        success_step += f" → 取首字母({raw_result}→{char})"
                elif raw_result.isalpha(): 
                    char = raw_result
                else:
                    char = '?'  # Changed: fallback to wildcard instead of O
                    success_step += f" → 兜底({raw_result}→?)"
            else:
                char = '?'  # Changed: default to wildcard when OCR completely fails
                success_step = "全部失败 → 兜底(?)"
            
            # 最终兜底修正
            if char == '0': 
                char = 'O'
                success_step += " → 0修正为O"
            
            results.append(char)
            
            # Store debug info with all attempts
            rack_debug_data.append({
                'index': i,
                'raw': raw_result if raw_result else '(failed)',
                'final': char,
                'process': success_step,
                'steps': attempt_steps  # Store all processing attempts
            })
        
        # Store rack debug info
        self.debug_info['rack_debug_data'] = rack_debug_data
        
        return results

    def ocr_board(self, img):
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)
        binary = cv2.dilate(binary, np.ones((3,3), np.uint8), iterations=2)
        cnts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # Initialize logs
        logs = []
        
        # Stage 1: Initial contour filtering
        raw_tiles = []
        rejected_contours = []
        
        for c in cnts:
            x, y, cw, ch = cv2.boundingRect(c)
            reason = ""
            
            # 放宽对细字母的限制，特别是字母I
            if cw <= 6 or ch <= 6:  # 从10改为6，允许更细的字母
                reason = "too_small"
            elif cw >= 150 or ch >= 150:
                reason = "too_large"
            elif not (0.1 < cw/float(ch) < 5.0):  # 放宽宽高比，从0.2-2.0改为0.1-5.0
                reason = f"bad_ratio_{cw/float(ch):.2f}"
            else:
                raw_tiles.append((x, y, cw, ch))
                continue
                
            rejected_contours.append({
                'bbox': (x, y, cw, ch),
                'reason': reason,
                'area': cw * ch
            })
        
        logs.append(f"🔍 轮廓过滤: {len(cnts)} 总轮廓 → {len(raw_tiles)} 候选块, {len(rejected_contours)} 被拒绝")
        for r in rejected_contours[:5]:  # Show first 5 rejections
            logs.append(f"   拒绝: {r['bbox']} - {r['reason']} (area={r['area']})")
        
        # Sort raw tiles by position (top-to-bottom, left-to-right)
        raw_tiles.sort(key=lambda t: (t[1], t[0]))
        
        final_tiles = []
        all_debug_tiles = []  # Store ALL tiles for debug (including filtered ones)
        
        if len(raw_tiles) > 1:
            # 1. Calculate centers and min distances for all raw tiles
            tile_data = []
            all_min_dists = []
            
            for i, t1 in enumerate(raw_tiles):
                c1 = (t1[0]+t1[2]//2, t1[1]+t1[3]//2)
                min_d = float('inf')
                for j, t2 in enumerate(raw_tiles):
                    if i!=j:
                        c2 = (t2[0]+t2[2]//2, t2[1]+t2[3]//2)
                        d = math.sqrt((c1[0]-c2[0])**2 + (c1[1]-c2[1])**2)
                        if d < min_d: min_d = d
                
                tile_data.append({'tile': t1, 'min_d': min_d, 'index': i})
                if min_d != float('inf'):
                    all_min_dists.append(min_d)
            
            # 2. Estimate step size (median of nearest neighbor distances)
            est_step = w / 15.0 # Default fallback
            if all_min_dists:
                # Filter out very small distances (overlapping noise)
                valid_dists = [d for d in all_min_dists if d > 10] 
                if valid_dists:
                    est_step = np.median(valid_dists)
            
            # 3. Filter based on step size
            thresh = est_step * 2.5
            logs.append(f"🔍 距离过滤: Est Step={est_step:.1f}, Thresh={thresh:.1f}")
            
            kept_count = 0
            filtered_count = 0
            
            for item in tile_data:
                if item['min_d'] < thresh:
                    final_tiles.append(item['tile'])
                    all_debug_tiles.append({'tile': item['tile'], 'filtered': False, 'min_d': item['min_d'], 'index': item['index']})
                    kept_count += 1
                else:
                    all_debug_tiles.append({'tile': item['tile'], 'filtered': True, 'min_d': item['min_d'], 'index': item['index']})
                    filtered_count += 1
                    logs.append(f"   过滤块 #{item['index']}: min_d={item['min_d']:.1f} > thresh={thresh:.1f}")
            
            logs.append(f"🔍 最终结果: {kept_count} 保留, {filtered_count} 过滤")
        else: 
            final_tiles = raw_tiles
            for i, t in enumerate(raw_tiles):
                all_debug_tiles.append({'tile': t, 'filtered': False, 'min_d': 0, 'index': i})
        
        detected_raw = []
        debug_tiles_data = [] # Store debug info for each tile

        logs.append(f"📝 开始 OCR 处理 {len(all_debug_tiles)} 个块...")
        
        # Process ALL tiles for debug (both filtered and unfiltered)
        for debug_item in all_debug_tiles:
            x, y, cw, ch = debug_item['tile']
            cx, cy = x + cw//2, y + ch//2
            size = max(cw, ch) + 5
            half = size // 2
            y1, y2 = max(0, cy-half), min(h, cy+half)
            x1, x2 = max(0, cx-half), min(w, cx+half)
            roi = img[y1:y2, x1:x2]
            
            if roi.size == 0: 
                logs.append(f"   跳过块 #{debug_item['index']}: ROI 为空")
                continue
            
            # Debug: Keep original ROI
            roi_orig = roi.copy()
            
            char = ""
            raw_ocr = ""
            correction_step = ""
            processing_steps = []
            
            # Step 1: Try with original ROI first (just resize)
            try:
                roi_resized = cv2.resize(roi_orig, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                res = self.reader.readtext(roi_resized, detail=0, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
                if res and len(res[0].strip()) > 0:
                    raw_ocr = res[0].upper()
                    processing_steps.append("原图×2")
                    logs.append(f"   块 #{debug_item['index']}: 原图识别成功 '{raw_ocr}'")
                else:
                    processing_steps.append("原图×2(失败)")
            except Exception as e:
                processing_steps.append(f"原图×2(错误:{str(e)[:10]})")
            
            # Step 2: If failed, try with grayscale + threshold
            if not raw_ocr:
                try:
                    roi_gray = cv2.cvtColor(roi_orig, cv2.COLOR_BGR2GRAY)
                    _, roi_bin = cv2.threshold(roi_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    roi_bin_resized = cv2.resize(roi_bin, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                    res = self.reader.readtext(roi_bin_resized, detail=0, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
                    if res and len(res[0].strip()) > 0:
                        raw_ocr = res[0].upper()
                        processing_steps.append("二值化×3")
                        logs.append(f"   块 #{debug_item['index']}: 二值化识别成功 '{raw_ocr}'")
                    else:
                        processing_steps.append("二值化×3(失败)")
                except Exception as e:
                    processing_steps.append(f"二值化×3(错误:{str(e)[:10]})")
            
            # Step 3: If still failed, try with BINARY_INV + erosion
            if not raw_ocr:
                try:
                    roi_gray = cv2.cvtColor(roi_orig, cv2.COLOR_BGR2GRAY)
                    _, roi_bin_inv = cv2.threshold(roi_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
                    roi_thin = cv2.erode(roi_bin_inv, np.ones((2,2), np.uint8), iterations=1)
                    roi_pad = cv2.copyMakeBorder(roi_thin, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=(0,0,0))
                    res = self.reader.readtext(roi_pad, detail=0, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
                    if res and len(res[0].strip()) > 0:
                        raw_ocr = res[0].upper()
                        processing_steps.append("反色腐蚀+边框")
                        logs.append(f"   块 #{debug_item['index']}: 反色腐蚀识别成功 '{raw_ocr}'")
                    else:
                        processing_steps.append("反色腐蚀+边框(失败)")
                except Exception as e:
                    processing_steps.append(f"反色腐蚀+边框(错误:{str(e)[:10]})")
            
            # Step 4: If still failed, try with more aggressive preprocessing
            if not raw_ocr:
                try:
                    roi_gray = cv2.cvtColor(roi_orig, cv2.COLOR_BGR2GRAY)
                    # Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
                    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
                    roi_enhanced = clahe.apply(roi_gray)
                    _, roi_bin = cv2.threshold(roi_enhanced, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
                    # More aggressive erosion
                    roi_thin = cv2.erode(roi_bin, np.ones((3,3), np.uint8), iterations=1)
                    roi_pad = cv2.copyMakeBorder(roi_thin, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=(0,0,0))
                    roi_pad_resized = cv2.resize(roi_pad, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                    res = self.reader.readtext(roi_pad_resized, detail=0, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
                    if res and len(res[0].strip()) > 0:
                        raw_ocr = res[0].upper()
                        processing_steps.append("CLAHE+强腐蚀")
                        logs.append(f"   块 #{debug_item['index']}: 强化预处理识别成功 '{raw_ocr}'")
                    else:
                        processing_steps.append("CLAHE+强腐蚀(失败)")
                except Exception as e:
                    processing_steps.append(f"CLAHE+强腐蚀(错误:{str(e)[:10]})")
            
            # Apply character correction if OCR succeeded
            if raw_ocr:
                if raw_ocr in self.correction_map: 
                    char = self.correction_map[raw_ocr]
                    correction_step = f"{raw_ocr}→{char}"
                elif len(raw_ocr)>1 and raw_ocr[0].isalpha(): 
                    char = raw_ocr[0]
                    correction_step = f"{raw_ocr}→{char}(first)"
                elif raw_ocr.isalpha(): 
                    char = raw_ocr
                    correction_step = f"{raw_ocr}(direct)"
                else:
                    correction_step = f"{raw_ocr}→fallback"
            else:
                correction_step = "all_methods_failed"
            
            # Final fallback
            if not char: 
                char = '?'
                if not correction_step: correction_step = "empty→?"
                else: correction_step += "→?"
            elif char == '0': 
                char = 'O'
                correction_step += "→O"
            
            # Create combined processing info
            process_info = " | ".join(processing_steps)
            full_correction = f"{process_info} → {correction_step}"
            
            logs.append(f"   块 #{debug_item['index']}: 最终 '{raw_ocr}' → '{char}' ({process_info}) ({'过滤' if debug_item['filtered'] else '保留'})")
            
            # Only add to detected_raw if not filtered
            if not debug_item['filtered']:
                detected_raw.append({'cx': cx, 'cy': cy, 'char': char})
            
            # For debug visualization, use the most processed image that gave results
            debug_proc_img = roi_orig  # Default to original
            if "反色腐蚀" in process_info:
                try:
                    roi_gray = cv2.cvtColor(roi_orig, cv2.COLOR_BGR2GRAY)
                    _, roi_bin_inv = cv2.threshold(roi_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
                    roi_thin = cv2.erode(roi_bin_inv, np.ones((2,2), np.uint8), iterations=1)
                    debug_proc_img = cv2.copyMakeBorder(roi_thin, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=(0,0,0))
                except: pass
            elif "CLAHE" in process_info:
                try:
                    roi_gray = cv2.cvtColor(roi_orig, cv2.COLOR_BGR2GRAY)
                    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
                    roi_enhanced = clahe.apply(roi_gray)
                    _, roi_bin = cv2.threshold(roi_enhanced, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
                    roi_thin = cv2.erode(roi_bin, np.ones((3,3), np.uint8), iterations=1)
                    debug_proc_img = cv2.copyMakeBorder(roi_thin, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=(0,0,0))
                except: pass
            elif "二值化" in process_info:
                try:
                    roi_gray = cv2.cvtColor(roi_orig, cv2.COLOR_BGR2GRAY)
                    _, debug_proc_img = cv2.threshold(roi_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                except: pass
            
            # Collect debug data for ALL tiles
            debug_tiles_data.append({
                'orig': roi_orig,
                'proc': debug_proc_img,
                'raw': raw_ocr,
                'correction': full_correction,
                'final': char,
                'filtered': debug_item['filtered'],
                'min_d': debug_item['min_d'],
                'index': debug_item['index']
            })
        
        # Store debug statistics and logs
        self.debug_info['ocr_stats'] = {
            'total_contours': len(cnts),
            'passed_size_filter': len(raw_tiles),
            'rejected_contours': len(rejected_contours),
            'passed_distance_filter': len([d for d in all_debug_tiles if not d['filtered']]),
            'rejected_distance_filter': len([d for d in all_debug_tiles if d['filtered']])
        }
        self.debug_info['ocr_logs'] = logs
        
        # --- Generate Debug Image for OCR Tiles ---
        if VIS_SHOW_DEBUG and debug_tiles_data:
            cols_per_row = 6
            rows_needed = math.ceil(len(debug_tiles_data) / cols_per_row)
            cell_w = 140
            cell_h = 100
            viz_w = cols_per_row * cell_w
            viz_h = rows_needed * cell_h
            
            viz_img = np.ones((viz_h, viz_w, 3), dtype=np.uint8) * 240 # Light gray bg
            
            for idx, item in enumerate(debug_tiles_data):
                r = idx // cols_per_row
                c = idx % cols_per_row
                x_base = c * cell_w
                y_base = r * cell_h
                
                # Color based on filter status
                border_color = (50, 50, 200) if item['filtered'] else (50, 200, 50)
                bg_color = (250, 250, 255) if item['filtered'] else (250, 255, 250)
                
                # Fill background
                cv2.rectangle(viz_img, (x_base+2, y_base+2), (x_base+cell_w-2, y_base+cell_h-2), bg_color, -1)
                
                # Draw Original
                try:
                    orig_resized = cv2.resize(item['orig'], (35, 35))
                    viz_img[y_base+5:y_base+40, x_base+5:x_base+40] = orig_resized
                except: pass
                
                # Draw Processed
                try:
                    proc_resized = cv2.resize(item['proc'], (35, 35))
                    proc_bgr = cv2.cvtColor(proc_resized, cv2.COLOR_GRAY2BGR)
                    viz_img[y_base+5:y_base+40, x_base+50:x_base+85] = proc_bgr
                except: pass
                
                # Draw Text Info
                text_lines = [
                    f"#{item['index']} Raw: {item['raw'] if item['raw'] else '_'}",
                    f"Step: {item['correction']}",
                    f"Final: {item['final']}",
                    f"Dist: {item['min_d']:.1f}"
                ]
                
                if item['filtered']:
                    text_lines.append("FILTERED")
                
                for i, text in enumerate(text_lines):
                    y_text = y_base + 50 + i * 10
                    color = (200, 50, 50) if item['filtered'] and "FILTERED" in text else (30, 30, 30)
                    cv2.putText(viz_img, text, (x_base+5, y_text), cv2.FONT_HERSHEY_SIMPLEX, 0.3, color, 1)
                
                # Draw border
                cv2.rectangle(viz_img, (x_base, y_base), (x_base+cell_w, y_base+cell_h), border_color, 2)

            self.debug_info['ocr_tiles_debug'] = self._img_to_base64(viz_img)

        # --- New Grid Fitting Logic ---
        if not detected_raw:
            return [['' for _ in range(15)] for _ in range(15)]

        # 1. Calculate step size based on nearest neighbor distances
        dists = []
        for i, d1 in enumerate(detected_raw):
            min_d = float('inf')
            for j, d2 in enumerate(detected_raw):
                if i == j: continue
                d = math.sqrt((d1['cx']-d2['cx'])**2 + (d1['cy']-d2['cy'])**2)
                if d < min_d: min_d = d
            if min_d != float('inf'):
                dists.append(min_d)
        
        if not dists:
            step_size = w / 15.0
        else:
            dists.sort()
            # Filter out very small distances (noise)
            valid_dists = [d for d in dists if d > 10]
            if valid_dists:
                step_size = np.median(valid_dists)
            else:
                step_size = w / 15.0

        true_step_x = step_size
        true_step_y = step_size
        print(f"📏 估算步长: {true_step_x:.1f}")

        # 2. Determine Grid Bounds (Dynamic Size)
        anchor = detected_raw[0]
        min_c, max_c = 0, 0
        min_r, max_r = 0, 0
        
        temp_coords = []
        for d in detected_raw:
            c_rel = int(round((d['cx'] - anchor['cx']) / true_step_x))
            r_rel = int(round((d['cy'] - anchor['cy']) / true_step_y))
            temp_coords.append((c_rel, r_rel, d))
            min_c = min(min_c, c_rel)
            max_c = max(max_c, c_rel)
            min_r = min(min_r, r_rel)
            max_r = max(max_r, r_rel)
            
        # Extend by 4 grids in all directions
        pad = 4
        start_c = min_c - pad
        end_c = max_c + pad
        start_r = min_r - pad
        end_r = max_r + pad
        
        cols = end_c - start_c + 1
        rows = end_r - start_r + 1
        
        grid_origin_x = anchor['cx'] + start_c * true_step_x
        grid_origin_y = anchor['cy'] + start_r * true_step_y
        
        matrix = [['' for _ in range(cols)] for _ in range(rows)]
        
        debug_viz = img.copy() if VIS_SHOW_DEBUG else None
        
        if debug_viz is not None:
            # Draw grid lines
            for c in range(cols + 1):
                x = int(grid_origin_x + c * true_step_x - true_step_x/2)
                cv2.line(debug_viz, (x, 0), (x, h), (255, 255, 0), 1)
            for r in range(rows + 1):
                y = int(grid_origin_y + r * true_step_y - true_step_y/2)
                cv2.line(debug_viz, (0, y), (w, y), (255, 255, 0), 1)

        for (c_rel, r_rel, d) in temp_coords:
            c_idx = c_rel - start_c
            r_idx = r_rel - start_r
            if 0 <= c_idx < cols and 0 <= r_idx < rows:
                matrix[r_idx][c_idx] = d['char']
                if debug_viz is not None:
                     cv2.rectangle(debug_viz, (int(d['cx'])-10, int(d['cy'])-10), (int(d['cx'])+10, int(d['cy'])+10), (0,255,0), 1)
                     cv2.putText(debug_viz, d['char'], (int(d['cx'])-5, int(d['cy'])+5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,255), 1)

        if debug_viz is not None:
             # cv2.imwrite(f"{self.out_dir}/debug_grid_fit.png", debug_viz)
             self.debug_info['grid_fit'] = self._img_to_base64(debug_viz)
             self.debug_info['grid_params'] = {
                 'step_x': float(true_step_x),
                 'step_y': float(true_step_y),
                 'origin_x': float(grid_origin_x),
                 'origin_y': float(grid_origin_y),
                 'rows': rows,
                 'cols': cols
             }

        self.grid_params = (grid_origin_x, grid_origin_y, true_step_x, true_step_y, rows, cols)
        return matrix

    # 🎨 可视化 (增加多重得分类型支持)
    def visualize_batch(self, moves_list, board_matrix):
        if not self.grid_params or self.seg_board_img is None:
            print("⚠️ 无法绘制，缺少网格参数或图片")
            return None

        ox, oy, sx, sy, rows, cols = self.grid_params
        img = self.seg_board_img.copy()
        h_img, w_img = img.shape[:2]

        # 颜色配置: BGR
        # 1. Best Moves (Long/High Score) -> Blue/Green tones
        # 2. Short Moves -> Purple/Dark tones
        # 3. Multi-Word Moves -> Pink/Cyan tones (Bright!)
        
        # 这里的 moves_list 已经是混合了三种类型的列表
        # 我们根据 tag 来区分颜色 (需要在 moves 字典里加 tag)
        # 如果没有 tag，就 fallback 到原来的四角轮换
        
        default_styles = [
            {'offset': (-0.2, 0.2),  'color': (255, 100, 0),   'name': 'BL'},
            {'offset': (-0.2, -0.2), 'color': (0, 165, 255),   'name': 'TL'},
            {'offset': (0.2, -0.2),  'color': (0, 200, 0),     'name': 'TR'},
            {'offset': (0.2, 0.2),   'color': (128, 0, 128),   'name': 'BR'}
        ]
        
        # 特定类型的颜色重写 (覆盖 style['color'])
        type_colors = {
            'best': (255, 100, 0),    # Blue
            'short': (128, 0, 128),   # Purple
            'multi': (180, 105, 255)  # Hot Pink (BGR) -> 醒目!
        }

        # 计数器，用于轮换位置
        pos_counter = 0

        for move in moves_list:
            style = default_styles[pos_counter % 4]
            pos_counter += 1
            
            # 确定颜色
            m_type = move.get('type', 'best')
            bg_color = type_colors.get(m_type, style['color'])
            
            word = move['word']
            r_start, c_start = move['row'], move['col']
            
            for i, char in enumerate(word):
                r, c = r_start, c_start + i
                if 0 <= r < rows and 0 <= c < cols and board_matrix[r][c] == '':
                    cx = ox + c * sx
                    cy = oy + r * sy
                    dx, dy = style['offset']
                    px = int(cx + dx * sx)
                    py = int(cy + dy * sy)
                    
                    box_size = int(sx * 0.45)
                    x1, y1 = px - box_size//2, py - box_size//2
                    x2, y2 = x1 + box_size, y1 + box_size
                    
                    # 背景
                    cv2.rectangle(img, (x1, y1), (x2, y2), bg_color, -1)
                    cv2.rectangle(img, (x1, y1), (x2, y2), (255, 255, 255), 1)
                    
                    # 文字
                    font_scale = 0.5
                    thickness = 1
                    display_char = char.upper()
                    (tw, th), _ = cv2.getTextSize(display_char, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
                    tx = px - tw // 2
                    ty = py + th // 2
                    
                    # 万能牌黑字，普通牌白字
                    text_color = (0, 0, 0) if char.islower() else (255, 255, 255)
                    cv2.putText(img, display_char, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, font_scale, text_color, thickness)

        # --- Crop Logic: Crop to grid area with padding ---
        pad_x = int(sx * 0.5)
        pad_y = int(sy * 0.5)
        
        x1 = int(ox - pad_x)
        y1 = int(oy - pad_y)
        x2 = int(ox + cols * sx + pad_x)
        y2 = int(oy + rows * sy + pad_y)
        
        # Clamp to image boundaries
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w_img, x2)
        y2 = min(h_img, y2)
        
        if x2 > x1 and y2 > y1:
            img = img[y1:y2, x1:x2]
        # --------------------------------------------------

        out_path = f"{self.out_dir}/board_result_final.png"
        # cv2.imwrite(out_path, img) # Optional: skip disk write
        print(f"✨ 结果已保存: {out_path}")
        return img

# ==============================================================================
# 🚀 辅助函数
# ==============================================================================
def get_diverse_moves(moves, top_n=3, min_dist=3):
    selected = []
    for m in moves:
        is_far_enough = True
        for s in selected:
            dist = math.sqrt((m['row'] - s['row'])**2 + (m['col'] - s['col'])**2)
            if dist < min_dist:
                is_far_enough = False
                break
        if is_far_enough: selected.append(m)
        if len(selected) >= top_n: break
    return selected

# ==============================================================================
# API Logic
# ==============================================================================

gaddag_instance = None

def get_gaddag():
    global gaddag_instance
    if gaddag_instance is None:
        dict_path = DICT_FILE if os.path.exists(DICT_FILE) else None
        gaddag_instance = GADDAG(dict_path)
    return gaddag_instance

@bp.route("/letter")
def letter_ui():
    """字母游戏页面"""
    return render_template("letter.html")

@bp.route("/api/letter/process", methods=["POST"])
def process_letter_image():
    """处理上传的图片 (Streaming Response)"""
    data = request.get_json()
    if not data or 'image' not in data:
        return jsonify({"status": "error", "message": "No image data provided"}), 400

    # 获取参数
    image_data = data['image']
    use_wildcard = data.get('use_wildcard', False)
    rec_top_n = int(data.get('rec_top_n', REC_TOP_N))
    rec_short_n = int(data.get('rec_short_n', REC_SHORT_N))
    rec_multi_n = int(data.get('rec_multi_n', REC_MULTI_N))
    min_dist = int(data.get('min_dist', MIN_DIST))
    short_len = int(data.get('short_len', SHORT_LEN))

    def generate():
        try:
            yield json.dumps({"type": "step", "msg": "正在解码图片..."}) + "\n"
            
            # 处理 Base64 图片
            img_b64 = image_data
            if "," in img_b64:
                img_b64 = img_b64.split(",")[1]
            
            image_bytes = base64.b64decode(img_b64)
            pil_image = Image.open(io.BytesIO(image_bytes))
            img_np = np.array(pil_image)
            img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
            
            vision = LetterLeagueVision()
            vision.out_dir = "/tmp/letter_league_output" 
            os.makedirs(vision.out_dir, exist_ok=True)
            
            # 1. Segmentation
            yield json.dumps({"type": "step", "msg": "正在分割棋盘与字母架..."}) + "\n"
            board_img, rack_img = vision.segment_image(img_bgr, LOGO_IMAGE)
            vision.seg_board_img = board_img
            
            debug_data = {}
            if board_img is not None and board_img.size > 0:
                debug_data['seg_board'] = vision._img_to_base64(board_img)
            if rack_img is not None and rack_img.size > 0:
                debug_data['seg_rack'] = vision._img_to_base64(rack_img)
            debug_data['logo_detection'] = vision.debug_info.get('logo_detection')
            
            yield json.dumps({"type": "debug", "data": debug_data}) + "\n"

            # 2. OCR Rack
            yield json.dumps({"type": "step", "msg": "正在识别字母架..."}) + "\n"
            rack_letters = []
            if rack_img is not None and rack_img.size > 0:
                rack_letters = vision.ocr_rack(rack_img)
            
            yield json.dumps({"type": "debug", "data": {"rack_str": "".join(rack_letters)}}) + "\n"
            
            # Send rack debug info if available (including binary map)
            rack_debug = vision.debug_info.get('rack_debug_data', [])
            rack_contour_debug = vision.debug_info.get('rack_contour_debug')
            rack_binary_map = vision.debug_info.get('rack_binary_map')
            rack_binary_contour_debug = vision.debug_info.get('rack_binary_contour_debug')
            rack_y_filter = vision.debug_info.get('rack_y_filter')  # 新增
            rack_area_filter = vision.debug_info.get('rack_area_filter')  # 新增
            debug_data = {"rack_str": "".join(rack_letters)}
            if rack_debug:
                debug_data['rack_debug'] = rack_debug
            if rack_contour_debug:
                debug_data['rack_contour_debug'] = rack_contour_debug
            if rack_binary_map:
                debug_data['rack_binary_map'] = rack_binary_map
            if rack_binary_contour_debug:
                debug_data['rack_binary_contour_debug'] = rack_binary_contour_debug
            if rack_y_filter:  # 新增
                debug_data['rack_y_filter'] = rack_y_filter
            if rack_area_filter:  # 新增
                debug_data['rack_area_filter'] = rack_area_filter
            yield json.dumps({"type": "debug", "data": debug_data}) + "\n"

            # 3. OCR Board
            yield json.dumps({"type": "step", "msg": "正在识别棋盘布局..."}) + "\n"
            board_matrix = [['' for _ in range(15)] for _ in range(15)]
            if board_img is not None and board_img.size > 0:
                board_matrix = vision.ocr_board(board_img)
            
            grid_fit_b64 = vision.debug_info.get('grid_fit')
            ocr_tiles_debug_b64 = vision.debug_info.get('ocr_tiles_debug') # Get new debug img
            grid_params = vision.debug_info.get('grid_params')
            ocr_stats = vision.debug_info.get('ocr_stats')
            ocr_logs = vision.debug_info.get('ocr_logs', [])
            
            yield json.dumps({"type": "debug", "data": {
                "ocr_board_matrix": board_matrix,
                "grid_fit": grid_fit_b64,
                "ocr_tiles_debug": ocr_tiles_debug_b64, # Send it
                "ocr_stats": ocr_stats, # Add stats
                "ocr_logs": ocr_logs, # Add logs
                "grid_params": grid_params
            }}) + "\n"

            # 4. Solving
            yield json.dumps({"type": "step", "msg": "正在计算最佳走法..."}) + "\n"
            solver = ScrabbleSolver(get_gaddag())
            solver.set_board(board_matrix)
            
            # 基础 rack 字符串（OCR 识别的结果）
            rack_str = "".join(rack_letters)
            
            # 【逻辑修正】根据用户反馈调整万能牌逻辑
            # 不勾选：忽略 OCR 识别出的 '?' (视为误识别或用户不想用)
            # 勾选：保留 OCR 识别出的 '?' (直接使用 OCR 结果)
            if not use_wildcard:
                rack_str = rack_str.replace("?", "")
                yield json.dumps({"type": "debug", "data": {"final_rack_str": rack_str, "wildcard_added": False}}) + "\n"
            else:
                # 勾选时直接使用 OCR 结果，不再额外添加 '?'
                # 只有当字符串中确实包含 '?' 时，才通知前端显示万能牌提示
                has_wildcard = '?' in rack_str
                yield json.dumps({"type": "debug", "data": {"final_rack_str": rack_str, "wildcard_added": has_wildcard}}) + "\n"
            
            moves = solver.solve(rack_str)
            
            # 5. Formatting Results
            results = {
                "best": [],
                "short": [],
                "multi": [],
                "result_image": None
            }
            
            final_viz_list = []
            if moves:
                # 1. Top Best
                diverse_top = get_diverse_moves(moves, top_n=rec_top_n, min_dist=min_dist)
                for m in diverse_top:
                    m['type'] = 'best'
                    final_viz_list.append(m)
                    results["best"].append({"word": m['word'], "row": m['row'], "col": m['col'], "cross": m['cross']})

                # 2. Top Short
                short_moves = [m for m in moves if len(m['word']) <= short_len]
                diverse_short = get_diverse_moves(short_moves, top_n=rec_short_n, min_dist=min_dist)
                for m in diverse_short:
                    m['type'] = 'short'
                    final_viz_list.append(m)
                    results["short"].append({"word": m['word'], "row": m['row'], "col": m['col']})

                # 3. Top Multi
                multi_moves = [m for m in moves if m['cross'] > 0]
                multi_moves.sort(key=lambda x: x['cross'], reverse=True)
                diverse_multi = get_diverse_moves(multi_moves, top_n=rec_multi_n, min_dist=min_dist)
                for m in diverse_multi:
                    m['type'] = 'multi'
                    if m not in final_viz_list:
                        final_viz_list.append(m)
                    results["multi"].append({"word": m['word'], "cross": m['cross']})
                
                # Visualize
                res_img = vision.visualize_batch(final_viz_list, board_matrix)
                if res_img is not None:
                    _, buffer = cv2.imencode('.png', res_img)
                    img_str = base64.b64encode(buffer).decode('utf-8')
                    results["result_image"] = f"data:image/png;base64,{img_str}"

            yield json.dumps({"type": "result", "data": results}) + "\n"
            yield json.dumps({"type": "step", "msg": "完成!"}) + "\n"

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    return Response(generate(), mimetype='application/x-ndjson')
