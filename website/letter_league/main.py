import cv2
import numpy as np
import easyocr
import os
import math
import time
from collections import defaultdict

# ==============================================================================
# ⚙️ 用户配置区域 (USER CONFIGURATION) - 在这里手动调整
# ==============================================================================

# 📁 路径配置
INPUT_IMAGE = "./test.png"       # 游戏截图路径
LOGO_IMAGE  = "./test_logo.png"  # Logo 模板路径
DICT_FILE   = "./twl06_ENABLE.txt"      # 字典文件路径
OUTPUT_DIR  = "./output"         # 输出目录 (不再使用 combined 子文件夹)

# 🤖 推荐算法配置
REC_TOP_N   = 6    # 推荐前几名最佳走法 (比如 3 或 5)
REC_SHORT_N = 6    # 推荐前几名短走法 (用于防守或清牌)
MIN_DIST    = 1    # 走法之间的最小格距离 (避免推荐挤在一起，设为 0 则不限制)
SHORT_LEN   = 4    # 定义"短走法"的最大长度 (<= 这个长度算短词)

# 🎨 可视化配置
VIS_SHOW_DEBUG = True  # 是否保存中间的调试图 (如网格线、分割图)

# ==============================================================================
# 🧠 第一部分：GADDAG 核心算法 & 求解器
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
            print("⚠️ 使用内置微型字典 (建议下载 twl06.txt) ...")
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
        path = word[::-1] + self.delimiter
        node = self.root
        for c in path:
            if c not in node.edges: return False
            node = node.edges[c]
        return node.is_end

class ScrabbleSolver:
    def __init__(self, gaddag):
        self.g = gaddag
        self.board = [['' for _ in range(15)] for _ in range(15)]
        self.rack = []
        self.results = []
        self.N = 15
        self.cross_sets = [[set() for _ in range(15)] for _ in range(15)]

    def set_board(self, board_matrix):
        self.board = board_matrix

    def solve(self, rack_str):
        self.rack = list(rack_str.upper())
        self.results = []
        self._compute_cross_sets()
        for row in range(self.N):
            self._gen_row(row)
            
        unique = {}
        for res in self.results:
            key = f"{res['word']}_{res['row']}_{res['col']}"
            if key not in unique: unique[key] = res
        
        return sorted(unique.values(), key=lambda x: len(x['word']), reverse=True)

    def _compute_cross_sets(self):
        full_set = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        for r in range(self.N):
            for c in range(self.N):
                if self.board[r][c] == '':
                    self.cross_sets[r][c] = full_set.copy()
                else:
                    self.cross_sets[r][c] = set()
        
        for c in range(self.N):
            for r in range(self.N):
                if self.board[r][c] == '':
                    top = (r > 0 and self.board[r-1][c] != '')
                    bottom = (r < self.N-1 and self.board[r+1][c] != '')
                    
                    if top or bottom:
                        valid = set()
                        start = r
                        while start > 0 and self.board[start-1][c] != '': start -= 1
                        end = r
                        while end < self.N-1 and self.board[end+1][c] != '': end += 1
                        
                        prefix = "".join([self.board[k][c] for k in range(start, r)])
                        suffix = "".join([self.board[k][c] for k in range(r+1, end+1)])
                        
                        for char in full_set:
                            candidate = prefix + char + suffix
                            if self.g.contains(candidate):
                                valid.add(char)
                        self.cross_sets[r][c] = valid

    def _gen_row(self, row):
        line = self.board[row]
        anchors = []
        for i in range(self.N):
            if line[i] == '':
                has_neighbor = False
                if i>0 and line[i-1]!='': has_neighbor=True
                if i<14 and line[i+1]!='': has_neighbor=True
                if row>0 and self.board[row-1][i]!='': has_neighbor=True
                if row<14 and self.board[row+1][i]!='': has_neighbor=True
                if has_neighbor: anchors.append(i)
        
        if not anchors and all(c=='' for r_ in self.board for c in r_):
            anchors.append(7)

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
                        new_node = self.g.root.edges[char]
                        self._gen(row, anchor-1, char, new_node, anchor, "LEFT")
                        
                        if self.g.delimiter in new_node.edges:
                            right_node = new_node.edges[self.g.delimiter]
                            self._gen(row, anchor+1, char, right_node, anchor, "RIGHT")
                        
                        self.rack.append(to_remove)

    def _gen(self, row, col, word, node, anchor_pos, direction):
        if direction == "LEFT":
            if self.g.delimiter in node.edges:
                right_node = node.edges[self.g.delimiter]
                self._gen(row, anchor_pos + 1, word, right_node, anchor_pos, "RIGHT")
            
            if col >= 0:
                char_on_board = self.board[row][col]
                if char_on_board != '': 
                    if char_on_board in node.edges:
                        self._gen(row, col - 1, char_on_board + word, node.edges[char_on_board], anchor_pos, "LEFT")
                else: 
                    allowed = self.cross_sets[row][col]
                    unique_rack = set(self.rack)
                    candidates = allowed if '?' in unique_rack else allowed.intersection(unique_rack)

                    for char in candidates:
                        if char in node.edges:
                            to_remove = char if char in self.rack else '?'
                            if to_remove in self.rack:
                                self.rack.remove(to_remove)
                                self._gen(row, col - 1, char + word, node.edges[char], anchor_pos, "LEFT")
                                self.rack.append(to_remove)

        elif direction == "RIGHT":
            if node.is_end:
                if col >= 15 or self.board[row][col] == '':
                    start_col = col - len(word)
                    if start_col >= 0 and col <= 15:
                         self.results.append({'word': word, 'row': row, 'col': start_col})

            if col < 15:
                char_on_board = self.board[row][col]
                if char_on_board != '':
                    if char_on_board in node.edges:
                        self._gen(row, col + 1, word + char_on_board, node.edges[char_on_board], anchor_pos, "RIGHT")
                else:
                    allowed = self.cross_sets[row][col]
                    unique_rack = set(self.rack)
                    candidates = allowed if '?' in unique_rack else allowed.intersection(unique_rack)
                        
                    for char in candidates:
                        if char in node.edges:
                            to_remove = char if char in self.rack else '?'
                            if to_remove in self.rack:
                                self.rack.remove(to_remove)
                                self._gen(row, col + 1, word + char, node.edges[char], anchor_pos, "RIGHT")
                                self.rack.append(to_remove)


# ==============================================================================
# 👁️ 第二部分：OCR 视觉层 (适配新配置)
# ==============================================================================

class LetterLeagueVision:
    def __init__(self):
        print("👁️ 初始化视觉模块...")
        self.reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        # 修改：直接使用配置的输出目录
        self.out_dir = OUTPUT_DIR
        os.makedirs(self.out_dir, exist_ok=True)
        self.correction_map = {'0': 'O', '8': 'B', '6': 'G', '5': 'S', '1': 'I', '2': 'Z'}
        self.grid_params = None 
        self.seg_board_img = None 

    def process_full_pipeline(self, img_path, logo_path):
        img = cv2.imread(img_path)
        if img is None: raise FileNotFoundError(f"无法读取: {img_path}")
        board_img, rack_img = self.segment_image(img, logo_path)
        self.seg_board_img = board_img 
        rack_letters = []
        if rack_img is not None and rack_img.size > 0:
            rack_letters = self.ocr_rack(rack_img)
        board_matrix = [['' for _ in range(15)] for _ in range(15)]
        if board_img is not None and board_img.size > 0:
            board_matrix = self.ocr_board(board_img)
        return board_matrix, rack_letters

    def segment_image(self, img, logo_path):
        h_img, w_img = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        lx, ly, lh, lw = 0, 0, 0, 0
        found_logo = False
        if os.path.exists(logo_path):
            tpl_l = cv2.imread(logo_path, 0)
            res = cv2.matchTemplate(gray, tpl_l, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, maxloc = cv2.minMaxLoc(res)
            if maxv > 0.7:
                lx, ly = maxloc
                lh, lw = tpl_l.shape
                found_logo = True
                print(f"⚓ Logo 匹配成功 (分值: {maxv:.2f})")
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
        
        # 只在 debug 开启时保存中间图片
        if VIS_SHOW_DEBUG:
            if board.size > 0: cv2.imwrite(f"{self.out_dir}/debug_seg_board.png", board)
            if rack.size > 0: cv2.imwrite(f"{self.out_dir}/debug_seg_rack.png", rack)
            
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
        matrix = [['' for _ in range(15)] for _ in range(15)]
        est_step_x = w / 15.0
        est_step_y = h / 15.0
        grid_origin_x, grid_origin_y = est_step_x/2, est_step_y/2
        true_step_x, true_step_y = est_step_x, est_step_y
        if detected_raw:
            xs = sorted([d['cx'] for d in detected_raw])
            ys = sorted([d['cy'] for d in detected_raw])
            gaps_x = [xs[i+1]-xs[i] for i in range(len(xs)-1)]
            gaps_y = [ys[i+1]-ys[i] for i in range(len(ys)-1)]
            valid_gaps_x = [g for g in gaps_x if 0.5*est_step_x < g < 1.5*est_step_x]
            valid_gaps_y = [g for g in gaps_y if 0.5*est_step_y < g < 1.5*est_step_y]
            if valid_gaps_x: true_step_x = np.median(valid_gaps_x)
            if valid_gaps_y: true_step_y = np.median(valid_gaps_y)
            print(f"📏 真实步长: {true_step_x:.1f} x {true_step_y:.1f}")
            center_x, center_y = w/2, h/2
            anchor_tile = min(detected_raw, key=lambda d: abs(d['cx']-center_x) + abs(d['cy']-center_y))
            anchor_col_idx = int(anchor_tile['cx'] / est_step_x)
            anchor_row_idx = int(anchor_tile['cy'] / est_step_y)
            anchor_col_idx = max(0, min(14, anchor_col_idx))
            anchor_row_idx = max(0, min(14, anchor_row_idx))
            grid_origin_x = anchor_tile['cx'] - anchor_col_idx * true_step_x
            grid_origin_y = anchor_tile['cy'] - anchor_row_idx * true_step_y
            
            debug_viz = img.copy() if VIS_SHOW_DEBUG else None
            
            for d in detected_raw:
                c_idx = int(round((d['cx'] - grid_origin_x) / true_step_x))
                r_idx = int(round((d['cy'] - grid_origin_y) / true_step_y))
                c_idx = max(0, min(14, c_idx))
                r_idx = max(0, min(14, r_idx))
                matrix[r_idx][c_idx] = d['char']
                
                if debug_viz is not None:
                     cv2.rectangle(debug_viz, (int(d['cx'])-10, int(d['cy'])-10), (int(d['cx'])+10, int(d['cy'])+10), (0,255,0), 1)

            if debug_viz is not None:
                 cv2.imwrite(f"{self.out_dir}/debug_grid_fit.png", debug_viz)

        self.grid_params = (grid_origin_x, grid_origin_y, true_step_x, true_step_y)
        return matrix

    # 🎨 四角轮换可视化
    def visualize_batch(self, moves_list, board_matrix):
        if not self.grid_params or self.seg_board_img is None:
            print("⚠️ 无法绘制，缺少网格参数或图片")
            return

        ox, oy, sx, sy = self.grid_params
        img = self.seg_board_img.copy()

        # 配置：每个Rank对应的 (位置偏移, 颜色, 描述)
        # BL=(-0.25, 0.25), TL=(-0.25, -0.25), TR=(0.25, -0.25), BR=(0.25, 0.25)
        styles = [
            {'offset': (-0.2, 0.2),  'color': (255, 100, 0),   'name': 'BL (1st)'}, # Blue
            {'offset': (-0.2, -0.2), 'color': (0, 165, 255),   'name': 'TL (2nd)'}, # Orange
            {'offset': (0.2, -0.2),  'color': (0, 200, 0),     'name': 'TR (3rd)'}, # Green
            {'offset': (0.2, 0.2),   'color': (128, 0, 128),   'name': 'BR (4th)'}  # Purple
        ]

        # 遍历所有要画的走法
        for rank, move in enumerate(moves_list):
            style = styles[rank % 4]
            word = move['word']
            r_start, c_start = move['row'], move['col']
            
            for i, char in enumerate(word):
                r, c = r_start, c_start + i
                if 0 <= r < 15 and 0 <= c < 15 and board_matrix[r][c] == '':
                    cx = ox + c * sx
                    cy = oy + r * sy
                    
                    dx, dy = style['offset']
                    px = int(cx + dx * sx)
                    py = int(cy + dy * sy)
                    
                    box_size = int(sx * 0.45)
                    x1, y1 = px - box_size//2, py - box_size//2
                    x2, y2 = x1 + box_size, y1 + box_size
                    
                    cv2.rectangle(img, (x1, y1), (x2, y2), style['color'], -1)
                    cv2.rectangle(img, (x1, y1), (x2, y2), (255, 255, 255), 1)
                    
                    font_scale = 0.5
                    thickness = 1
                    (tw, th), _ = cv2.getTextSize(char, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
                    tx = px - tw // 2
                    ty = py + th // 2
                    cv2.putText(img, char, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), thickness)

        out_path = f"{self.out_dir}/board_result_final.png"
        cv2.imwrite(out_path, img)
        print(f"✨ 结果已保存: {out_path}")

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
# 🚀 主运行逻辑
# ==============================================================================

if __name__ == "__main__":
    try:
        vision = LetterLeagueVision()
        gaddag = GADDAG(DICT_FILE) 
        solver = ScrabbleSolver(gaddag)
        
        print("\n📸 开始视觉分析...")
        board, rack = vision.process_full_pipeline(INPUT_IMAGE, LOGO_IMAGE)
        
        print("\n🧩 识别到的棋盘:")
        for r in board:
            print(" ".join([c if c else '.' for c in r]))
        print(f"\n🔠 识别到的字母架: {rack}")
        
        print("\n🧠 AI 正在计算最佳走法...")
        solver.set_board(board)
        rack_str = "".join(rack).replace("?", "?")
        
        start_t = time.time()
        moves = solver.solve(rack_str)
        
        print(f"\n✅ 计算完成! 耗时 {time.time()-start_t:.3f}s, 找到 {len(moves)} 种走法")
        
        if moves:
            # 1. 筛选 Top N (使用配置参数)
            diverse_top = get_diverse_moves(moves, top_n=REC_TOP_N, min_dist=MIN_DIST)
            print(f"\n🏆 Top {REC_TOP_N} 最佳推荐:")
            for i, m in enumerate(diverse_top):
                print(f"  {i+1}. {m['word']} (Row {m['row']}, Col {m['col']}, Len {len(m['word'])})")

            # 2. 筛选 短词 Top N (使用配置参数)
            short_moves = [m for m in moves if len(m['word']) <= SHORT_LEN]
            diverse_short = get_diverse_moves(short_moves, top_n=REC_SHORT_N, min_dist=MIN_DIST)
            
            print(f"\n🐣 Top {REC_SHORT_N} 短走法 (<= {SHORT_LEN}):")
            for i, m in enumerate(diverse_short):
                print(f"  {i+1}. {m['word']} (Row {m['row']}, Col {m['col']})")

            # 3. 批量可视化 (合并列表)
            final_visualization_list = diverse_top + diverse_short
            vision.visualize_batch(final_visualization_list, board)
        else:
            print("❌ 未找到任何合法走法")
            
    except Exception as e:
        print(f"❌ 程序中断: {e}")
        import traceback
        traceback.print_exc()