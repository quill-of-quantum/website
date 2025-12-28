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
LOGO_IMAGE  = os.path.join(BASE_DIR, "test_logo.png")
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
            candidates = allowed if '?' in unique_rack else allowed.intersection(unique_rack)

            for char in candidates:
                if char in self.g.root.edges:
                    to_remove = char if char in self.rack else '?'
                    if to_remove in self.rack:
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
                    candidates = allowed if '?' in unique_rack else allowed.intersection(unique_rack)

                    for char in candidates:
                        if char in node.edges:
                            to_remove = char if char in self.rack else '?'
                            if to_remove in self.rack:
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
                    candidates = allowed if '?' in unique_rack else allowed.intersection(unique_rack)
                        
                    for char in candidates:
                        if char in node.edges:
                            to_remove = char if char in self.rack else '?'
                            if to_remove in self.rack:
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
        _, buffer = cv2.imencode('.png', img)
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
                if maxv > 0.7:
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
        game_h = game_bottom - game_top
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
        rack  = safe_crop(0.35, 0.87, 0.28, 0.11)
        # Removed file writing for web context, handled in process_full_pipeline
        return board, rack

    def ocr_rack(self, img):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced_gray = clahe.apply(gray)
        binary_map = cv2.adaptiveThreshold(enhanced_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        binary_map = cv2.erode(binary_map, np.ones((3,3), np.uint8), iterations=1)
        cnts, _ = cv2.findContours(binary_map, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        valid_boxes = []
        for c in cnts:
            x, y, cw, ch = cv2.boundingRect(c)
            if 30 < cw < 150 and 30 < ch < 150 and 0.8 < cw/float(ch) < 1.2:
                valid_boxes.append((x, y, cw, ch))
        valid_boxes.sort(key=lambda b: b[0])
        final_boxes = []
        for box in valid_boxes:
            if not final_boxes: final_boxes.append(box)
            else:
                last = final_boxes[-1]
                if abs(box[0]-last[0]) < 20:
                    if box[2]*box[3] > last[2]*last[3]: final_boxes[-1] = box
                else: final_boxes.append(box)
        results = []
        for (x, y, cw, ch) in final_boxes:
            pad = 2
            tile_roi = img[y+pad : y+ch-pad, x+pad : x+cw-pad]
            th, tw = tile_roi.shape[:2]
            if th==0 or tw==0: continue
            roi = tile_roi[int(th*0.25):int(th*0.85), int(tw*0.15):int(tw*0.75)]
            if roi.size == 0: continue
            roi = cv2.resize(roi, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            _, roi_bin = cv2.threshold(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            char = "?"
            try:
                res = self.reader.readtext(roi_bin, detail=0, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ')
                if res:
                    c = res[0].upper()
                    if c == 'I' and 'L' in c: c = 'L'
                    char = c
            except: pass
            results.append(char)
        return results

    def ocr_board(self, img):
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)
        binary = cv2.dilate(binary, np.ones((3,3), np.uint8), iterations=2)
        cnts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        raw_tiles = []
        for c in cnts:
            x, y, cw, ch = cv2.boundingRect(c)
            if 10 < cw < 150 and 10 < ch < 150 and 0.2 < cw/float(ch) < 2.0:
                raw_tiles.append((x, y, cw, ch))
        final_tiles = []
        if len(raw_tiles) > 1:
            avg_s = sum([max(t[2], t[3]) for t in raw_tiles]) / len(raw_tiles)
            thresh = avg_s * 2.5
            for i, t1 in enumerate(raw_tiles):
                c1 = (t1[0]+t1[2]//2, t1[1]+t1[3]//2)
                min_d = float('inf')
                for j, t2 in enumerate(raw_tiles):
                    if i!=j:
                        c2 = (t2[0]+t2[2]//2, t2[1]+t2[3]//2)
                        d = math.sqrt((c1[0]-c2[0])**2 + (c1[1]-c2[1])**2)
                        if d < min_d: min_d = d
                if min_d < thresh: final_tiles.append(t1)
        else: final_tiles = raw_tiles
        detected_raw = []
        for (x, y, cw, ch) in final_tiles:
            cx, cy = x + cw//2, y + ch//2
            size = max(cw, ch) + 5
            half = size // 2
            y1, y2 = max(0, cy-half), min(h, cy+half)
            x1, x2 = max(0, cx-half), min(w, cx+half)
            roi = img[y1:y2, x1:x2]
            if roi.size == 0: continue
            roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            _, roi_bin = cv2.threshold(roi_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            roi_thin = cv2.erode(roi_bin, np.ones((2,2), np.uint8), iterations=1)
            roi_pad = cv2.copyMakeBorder(roi_thin, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=(0,0,0))
            char = ""
            try:
                res = self.reader.readtext(roi_pad, detail=0, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
                if res:
                    raw = res[0].upper()
                    if raw in self.correction_map: char = self.correction_map[raw]
                    elif len(raw)>1 and raw[0].isalpha(): char = raw[0]
                    elif raw.isalpha(): char = raw
            except: pass
            if not char: char = 'O'
            elif char == '0': char = 'O'
            detected_raw.append({'cx': cx, 'cy': cy, 'char': char})
        
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

            # 3. OCR Board
            yield json.dumps({"type": "step", "msg": "正在识别棋盘布局..."}) + "\n"
            board_matrix = [['' for _ in range(15)] for _ in range(15)]
            if board_img is not None and board_img.size > 0:
                board_matrix = vision.ocr_board(board_img)
            
            grid_fit_b64 = vision.debug_info.get('grid_fit')
            grid_params = vision.debug_info.get('grid_params')
            yield json.dumps({"type": "debug", "data": {
                "ocr_board_matrix": board_matrix,
                "grid_fit": grid_fit_b64,
                "grid_params": grid_params
            }}) + "\n"

            # 4. Solving
            yield json.dumps({"type": "step", "msg": "正在计算最佳走法..."}) + "\n"
            solver = ScrabbleSolver(get_gaddag())
            solver.set_board(board_matrix)
            
            rack_str = "".join(rack_letters).replace("?", "?")
            
            # Logic: If user checks "Use Wildcard", we append a wildcard.
            # This allows the user to manually add a blank tile if OCR missed it.
            if use_wildcard:
                rack_str += "?" 
            
            # Send debug update about the final rack used
            yield json.dumps({"type": "debug", "data": {"final_rack_str": rack_str}}) + "\n"
            
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
