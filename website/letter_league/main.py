import cv2
import numpy as np
import easyocr
import os
import math
import time

# ==============================================================================
# 🎯 第一部分：GADDAG 数据结构 & 求解器 (保持不变，这是大脑)
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
        
        # 加载字典
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
            
        # 构建 GADDAG
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
        self.rack = []
        
    def set_board(self, board_matrix):
        self.board = board_matrix

    def solve(self, rack_str):
        # 这里仅作演示占位，实际需要复杂的 GADDAG 搜索逻辑
        print(f"🧠 (Solver收到 Rack: {rack_str}，GADDAG Ready)")
        return []

# ==============================================================================
# 👁️ 第二部分：OCR 视觉层 (完全复刻你的三个文件)
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
        
        # 1. 严格复刻 test_orgin_segmentation.py
        board_img, rack_img = self.segment_image(img, logo_path)
        
        # 2. 严格复刻 test_rack_ocr.py
        rack_letters = self.ocr_rack(rack_img)
        
        # 3. 严格复刻 test_board_ocr.py
        board_matrix = self.ocr_board(board_img)
        
        return board_matrix, rack_letters

    def segment_image(self, img, logo_path):
        """
        来源：test_orgin_segmentation.py
        """
        h_img, w_img = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Logo 匹配逻辑
        if not os.path.exists(logo_path):
            print("❌ 找不到 Logo 模板，无法进行标准切割")
            return None, None
            
        tpl_l = cv2.imread(logo_path, 0)
        res = cv2.matchTemplate(gray, tpl_l, cv2.TM_CCOEFF_NORMED)
        _, maxv, _, maxloc = cv2.minMaxLoc(res)
        
        lx, ly = maxloc
        lh, lw = tpl_l.shape
        print(f"match score logo_l: {maxv} at {(lx, ly)} size {(lw, lh)}")
        
        # ========== 参数严格来自 test_orgin_segmentation.py ==========
        game_left = lx
        game_width = w_img - game_left
        
        TOP_RATIO = 0.10
        game_top = int(ly + lh * TOP_RATIO)
        
        GAME_H_RATIO = 0.5
        game_height = int(game_width * GAME_H_RATIO)
        # 注意：原文件里有 min(H, ...)，这里保留逻辑
        game_bottom = min(h_img, game_top + game_height)
        
        game_w = game_width
        game_h = game_bottom - game_top
        
        # 内部分区比例
        BOARD_X0 = 0.00
        BOARD_Y0 = 0.13
        BOARD_W  = 0.9
        BOARD_H  = 0.70
        
        RACK_X0  = 0.35
        RACK_Y0  = 0.87
        RACK_W   = 0.28
        RACK_H   = 0.11
        
        def crop_ratio(x0, y0, w0, h0):
            x = int(game_left + x0 * game_w)
            y = int(game_top  + y0 * game_h)
            w = int(w0 * game_w)
            h = int(h0 * game_h)
            # 安全检查 (防止越界报错)
            y1, y2 = max(0, y), min(h_img, y+h)
            x1, x2 = max(0, x), min(w_img, x+w)
            if y2<=y1 or x2<=x1: return np.array([])
            return img[y1:y2, x1:x2]

        board = crop_ratio(BOARD_X0, BOARD_Y0, BOARD_W, BOARD_H)
        rack  = crop_ratio(RACK_X0,  RACK_Y0,  RACK_W,  RACK_H)
        
        cv2.imwrite(f"{self.out_dir}/seg_board.png", board)
        cv2.imwrite(f"{self.out_dir}/seg_rack.png", rack)
        
        return board, rack

    def ocr_rack(self, img):
        """
        来源：test_rack_ocr.py
        """
        if img is None or img.size == 0: return []
        
        # 图像预处理 (严格参数)
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
            # 参数: 30 < cw < 150 ...
            if 30 < cw < 150 and 30 < ch < 150:
                ratio = cw / float(ch)
                if 0.8 < ratio < 1.2:
                    valid_boxes.append((x, y, cw, ch))
                    
        valid_boxes = sorted(valid_boxes, key=lambda b: b[0])
        
        # 去重逻辑 (来自 test_rack_ocr.py)
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
                    
        results = []
        for i, (x, y, cw, ch) in enumerate(final_boxes):
            # 1. 基础切割
            pad = 2 # test_rack_ocr.py 中 pad_x=8 pad_y=5? 
            # 等等，你的 test_rack_ocr.py snippet 里写的是 pad = 2 
            # 见 snippet: "pad = 2 \n tile_roi = img[y+pad : y+ch-pad, x+pad : x+cw-pad]"
            # 之前的 pad_x=8 是更早版本的，我遵循你最后提供的 snippet
            
            tile_roi = img[y+pad : y+ch-pad, x+pad : x+cw-pad]
            th, tw, _ = tile_roi.shape
            
            # ----------------------------------------------------
            # ✂️ 核心修改：Sub-cropping (严格参数)
            # ----------------------------------------------------
            crop_y1 = int(th * 0.25)
            crop_y2 = int(th * 0.85)
            crop_x1 = int(tw * 0.15)
            crop_x2 = int(tw * 0.75)
            
            letter_roi = tile_roi[crop_y1:crop_y2, crop_x1:crop_x2]
            
            # 2. 图像增强 (放大 + 灰度 + Otsu)
            roi_zoom = cv2.resize(letter_roi, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            roi_gray = cv2.cvtColor(roi_zoom, cv2.COLOR_BGR2GRAY)
            _, roi_binary = cv2.threshold(roi_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            
            # 3. 识别
            char = "_"
            try:
                res = self.reader.readtext(roi_binary, detail=0, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ')
                if res:
                    char = res[0]
                    # 常见误识别修正 (来自 snippet)
                    if char == 'I' and 'L' in char: char = 'L'
            except: pass
            
            results.append(char)
            
        return results

    def ocr_board(self, img):
        """
        来源：test_board_ocr.py
        """
        if img is None or img.size == 0: return []
        h, w = img.shape[:2]
        
        # 步骤 1: 定位 (Dark Ink Extraction)
        # 参数严格复刻: threshold 80, INV
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, binary_finder = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)
        
        # 参数严格复刻: dilate iterations=2
        kernel_finder = np.ones((3,3), np.uint8)
        binary_finder = cv2.dilate(binary_finder, kernel_finder, iterations=2)
        
        cnts, _ = cv2.findContours(binary_finder, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        raw_tiles = []
        for c in cnts:
            x, y, cw, ch = cv2.boundingRect(c)
            # 基础过滤 (参数严格复刻)
            if not (10 < cw < 150 and 10 < ch < 150): continue
            ratio = cw / float(ch)
            if not (0.2 < ratio < 2.0): continue
            raw_tiles.append((x, y, cw, ch))
            
        # 步骤 2: 剔除离群点
        final_tiles = []
        if len(raw_tiles) > 1:
            avg_size = sum([max(t[2], t[3]) for t in raw_tiles]) / len(raw_tiles)
            # 参数: 2.5 倍
            distance_threshold = avg_size * 2.5
            
            for i, t1 in enumerate(raw_tiles):
                c1_x = t1[0] + t1[2] // 2
                c1_y = t1[1] + t1[3] // 2
                min_dist = float('inf')
                
                for j, t2 in enumerate(raw_tiles):
                    if i == j: continue
                    c2_x = t2[0] + t2[2] // 2
                    c2_y = t2[1] + t2[3] // 2
                    dist = math.sqrt((c1_x - c2_x)**2 + (c1_y - c2_y)**2)
                    if dist < min_dist: min_dist = dist
                    
                if min_dist < distance_threshold:
                    final_tiles.append(t1)
        else:
            final_tiles = raw_tiles
            
        final_tiles = sorted(final_tiles, key=lambda b: (b[1], b[0]))
        
        # 步骤 3: 识别 + 排除法兜底
        detected_chars = []
        
        for i, (x, y, cw, ch) in enumerate(final_tiles):
            center_x = x + cw // 2
            center_y = y + ch // 2
            # 参数: max(cw, ch) + 5
            size = max(cw, ch) + 5
            half = size // 2
            
            y1, y2 = max(0, center_y - half), min(h, center_y + half)
            x1, x2 = max(0, center_x - half), min(w, center_x + half)
            
            roi = img[y1:y2, x1:x2]
            roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            
            # 二值化 + 瘦身
            _, roi_binary = cv2.threshold(roi_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            kernel_thin = np.ones((2,2), np.uint8)
            roi_thin = cv2.erode(roi_binary, kernel_thin, iterations=1)
            # 参数: copyMakeBorder 10
            roi_padded = cv2.copyMakeBorder(roi_thin, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=(0,0,0))
            
            char = ""
            safe_allowlist = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
            
            try:
                res = self.reader.readtext(roi_padded, detail=0, allowlist=safe_allowlist)
                if res:
                    raw = res[0].upper()
                    if raw in self.correction_map: char = self.correction_map[raw]
                    elif len(raw) > 1 and raw[0].isalpha(): char = raw[0]
                    elif raw.isalpha(): char = raw
            except: pass
            
            if not char: char = 'O'
            elif char == '0': char = 'O'
            
            # 坐标映射 (简单假设 15x15)
            # 这里需要一个映射逻辑，因为 test_board_ocr.py 只输出了列表，没做矩阵映射
            # 我这里补充一个简单的映射以适配 main.py 的输出格式
            cell_w_est = w / 15.0
            cell_h_est = h / 15.0
            r_idx = min(14, max(0, int(center_y / cell_h_est)))
            c_idx = min(14, max(0, int(center_x / cell_w_est)))
            
            detected_chars.append((r_idx, c_idx, char))
            
        matrix = [['' for _ in range(15)] for _ in range(15)]
        for r, c, val in detected_chars:
            matrix[r][c] = val
            
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
        # 必须传入 logo 路径
        board, rack = vision.process_full_pipeline("./test.png", "./test_logo.png")
        
        print("\n🧩 识别到的棋盘:")
        for r in board:
            print(" ".join([c if c else '.' for c in r]))
        print(f"\n🔠 识别到的字母架: {rack}")
        
    except Exception as e:
        print(f"❌ 程序中断: {e}")
        import traceback
        traceback.print_exc()