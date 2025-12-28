import cv2
import numpy as np
import easyocr
import os
import math
import time
from collections import defaultdict

# ==============================================================================
# 🎯 第一部分：GADDAG 数据结构 & 求解器
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
            words = ["APPLE", "BANANA", "CAT", "DOG", "HELLO", "WORLD", "TEST", "LETTER", "LEAGUE", "CODE", "DATA", "GAME", "BOARD", "RACK"]
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

class ScrabbleSolver:
    def __init__(self, gaddag):
        self.g = gaddag
        self.board = [['' for _ in range(15)] for _ in range(15)]
    def set_board(self, board_matrix):
        self.board = board_matrix
    def solve(self, rack_str):
        print(f"🧠 (Solver收到 Rack: {rack_str}，准备计算...)")
        return []

# ==============================================================================
# 👁️ 第二部分：OCR 视觉层 (基于字母位置重建网格)
# ==============================================================================

class LetterLeagueVision:
    def __init__(self):
        print("👁️ 初始化视觉模块...")
        self.reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        self.out_dir = "./output/combined"
        os.makedirs(self.out_dir, exist_ok=True)
        self.correction_map = {'0': 'O', '8': 'B', '6': 'G', '5': 'S', '1': 'I', '2': 'Z'}

    def process_full_pipeline(self, img_path, logo_path="./test_logo.png"):
        img = cv2.imread(img_path)
        if img is None: raise FileNotFoundError(f"无法读取: {img_path}")
        
        # 1. 切割
        board_img, rack_img = self.segment_image(img, logo_path)
        
        # 2. Rack 识别
        rack_letters = []
        if rack_img is not None and rack_img.size > 0:
            rack_letters = self.ocr_rack(rack_img)
        
        # 3. Board 识别
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
            print("⚠️ 未找到 Logo，使用默认全屏参数")
            lx, ly, lh, lw = 0, 0, int(h_img*0.05), int(w_img*0.05)

        game_left = lx
        game_width = w_img - game_left
        
        TOP_RATIO = 0.10
        game_top = int(ly + lh * TOP_RATIO)
        
        GAME_H_RATIO = 0.5
        game_height = int(game_width * GAME_H_RATIO)
        game_bottom = min(h_img, game_top + game_height)
        
        game_w = game_width
        game_h = game_bottom - game_top
        
        BOARD_X0, BOARD_Y0, BOARD_W, BOARD_H = 0.00, 0.13, 0.9, 0.70
        RACK_X0, RACK_Y0, RACK_W, RACK_H = 0.35, 0.87, 0.28, 0.11
        
        def safe_crop(x0, y0, w0, h0, tag):
            x = int(game_left + x0 * game_w)
            y = int(game_top  + y0 * game_h)
            w = int(w0 * game_w)
            h = int(h0 * game_h)
            x1, x2 = max(0, x), min(w_img, x+w)
            y1, y2 = max(0, y), min(h_img, y+h)
            if x2 <= x1 or y2 <= y1: return np.array([])
            return img[y1:y2, x1:x2]

        board = safe_crop(BOARD_X0, BOARD_Y0, BOARD_W, BOARD_H, "Board")
        rack  = safe_crop(RACK_X0,  RACK_Y0,  RACK_W,  RACK_H, "Rack")
        
        if board.size > 0: cv2.imwrite(f"{self.out_dir}/seg_board.png", board)
        if rack.size > 0: cv2.imwrite(f"{self.out_dir}/seg_rack.png", rack)
        
        return board, rack

    def ocr_rack(self, img):
        # 严格复刻 test_rack_ocr.py
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
        
        detected_raw = [] # 暂存识别结果
        
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

        # =========================================================
        # 🧩 算法升级: "Letter-Centric Grid Fitting"
        # 直接利用识别出的字母位置来重建网格，无视图片切割误差
        # =========================================================
        matrix = [['' for _ in range(15)] for _ in range(15)]
        
        if not detected_raw:
            return matrix

        # 1. 聚类 (Clustering) 确定"活跃列"和"活跃行"
        # 我们假设大概率 w/15 是一个合理的初始步长估计，用于判定"是否属于同一列"
        est_step_x = w / 15.0
        est_step_y = h / 15.0
        
        # 提取所有 X 坐标并排序
        xs = sorted([d['cx'] for d in detected_raw])
        ys = sorted([d['cy'] for d in detected_raw])
        
        # 计算字母间的真实间距 (Gap Analysis)
        # 我们寻找最近邻间距的中位数，这通常就是 1 个格子的真实宽度
        gaps_x = [xs[i+1]-xs[i] for i in range(len(xs)-1)]
        gaps_y = [ys[i+1]-ys[i] for i in range(len(ys)-1)]
        
        # 过滤掉太大的间距 (跨列了) 和太小的间距 (同一列的微小抖动)
        valid_gaps_x = [g for g in gaps_x if 0.5*est_step_x < g < 1.5*est_step_x]
        valid_gaps_y = [g for g in gaps_y if 0.5*est_step_y < g < 1.5*est_step_y]
        
        # 如果找不到 valid gaps (比如字母太稀疏)，回退到估计值
        true_step_x = np.median(valid_gaps_x) if valid_gaps_x else est_step_x
        true_step_y = np.median(valid_gaps_y) if valid_gaps_y else est_step_y
        
        print(f"📏 网格重建: 估算步长 {est_step_x:.1f}px -> 真实步长 {true_step_x:.1f}px")

        # 2. 锚点对齐 (Anchor Alignment)
        # 找一个靠近图片中心的字母作为"绝对锚点"，以它为基准向四周推导
        # 这样可以消除边缘切割误差带来的整体漂移
        center_x, center_y = w/2, h/2
        
        # 找到离中心最近的那个字母
        anchor_tile = min(detected_raw, key=lambda d: abs(d['cx']-center_x) + abs(d['cy']-center_y))
        
        # 估算这个锚点在 15x15 网格里的绝对坐标 (approximate)
        # 这里我们仍然需要依赖一次 w/15 来确定锚点大概是第几列
        # 但是只用这一次！一旦确定它是 (7,7) 或 (8,6)，后续所有坐标都相对于它计算
        anchor_col_idx = int(anchor_tile['cx'] / est_step_x)
        anchor_row_idx = int(anchor_tile['cy'] / est_step_y)
        
        # 修正锚点坐标 (Clamp to 0-14)
        anchor_col_idx = max(0, min(14, anchor_col_idx))
        anchor_row_idx = max(0, min(14, anchor_row_idx))
        
        # 推导网格的"真实零点" (Grid Origin)
        # Origin = AnchorPixel - AnchorIndex * TrueStep
        grid_origin_x = anchor_tile['cx'] - anchor_col_idx * true_step_x
        grid_origin_y = anchor_tile['cy'] - anchor_row_idx * true_step_y
        
        debug_viz = img.copy()
        
        # 3. 映射所有字母
        for d in detected_raw:
            # 公式: Index = round( (Pixel - Origin) / TrueStep )
            c_idx = int(round((d['cx'] - grid_origin_x) / true_step_x))
            r_idx = int(round((d['cy'] - grid_origin_y) / true_step_y))
            
            # 边界保护
            c_idx = max(0, min(14, c_idx))
            r_idx = max(0, min(14, r_idx))
            
            matrix[r_idx][c_idx] = d['char']
            
            # Debug
            cv2.circle(debug_viz, (int(d['cx']), int(d['cy'])), 5, (0,0,255), -1)
            cv2.putText(debug_viz, f"{r_idx},{c_idx}", (int(d['cx']), int(d['cy'])), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,0,0), 1)

        # 画出推导出的网格线
        for i in range(16):
            x = int(grid_origin_x + (i - 0.5) * true_step_x) # -0.5 是因为 grid_origin 是 col 0 的中心
            y = int(grid_origin_y + (i - 0.5) * true_step_y)
            # x, y 是格子的边界线
            # 修正一下显示: 我们的 grid_origin 是 col 0 的中心点坐标
            # 所以 col 0 的左边界是 origin - 0.5 * step
            # col i 的左边界是 origin + (i - 0.5) * step
            
            # 简单的画线逻辑
            vx = int(grid_origin_x + (i - 0.5) * true_step_x)
            vy = int(grid_origin_y + (i - 0.5) * true_step_y)
            
            cv2.line(debug_viz, (vx, 0), (vx, h), (0, 255, 255), 1)
            cv2.line(debug_viz, (0, vy), (w, vy), (0, 255, 255), 1)

        cv2.imwrite(f"{self.out_dir}/debug_board_grid.png", debug_viz)
        return matrix

# ==============================================================================
# 🚀 主运行逻辑
# ==============================================================================

if __name__ == "__main__":
    try:
        vision = LetterLeagueVision()
        gaddag = GADDAG("./twl06.txt") 
        solver = ScrabbleSolver(gaddag)
        
        print("\n📸 开始视觉分析...")
        board, rack = vision.process_full_pipeline("./test.png", "./test_logo.png")
        
        print("\n🧩 识别到的棋盘:")
        for r in board:
            print(" ".join([c if c else '.' for c in r]))
        print(f"\n🔠 识别到的字母架: {rack}")
        print(f"🖼️ 调试图已生成: ./output/combined/debug_board_grid.png")
        
    except Exception as e:
        print(f"❌ 程序中断: {e}")
        import traceback
        traceback.print_exc()