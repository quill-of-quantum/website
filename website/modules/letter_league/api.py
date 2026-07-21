from flask import Blueprint, render_template, request, jsonify, Response, send_file
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
import threading
from collections import defaultdict

bp = Blueprint("letter", __name__)

# 获取当前文件所在的目录绝对路径
BASE_DIR = "/home/bbdwz/projects/website/letter_league"

# ==============================================================================
# ⚙️ 用户配置区域 (USER CONFIGURATION)
# ==============================================================================

INPUT_IMAGE = os.path.join(BASE_DIR, "test.png")
LOGO_IMAGE  = os.path.join(BASE_DIR, "logo.png")
SHUFFLE_IMAGE = os.path.join(BASE_DIR, "shuffle.png")
DICT_FILE   = os.path.join(BASE_DIR, "twl06_ENABLE.txt")
COMMON_WORDS_FILE = os.path.join(BASE_DIR, "twl06_google10000.txt")
OUTPUT_DIR  = os.path.join(BASE_DIR, "output")
EXAMPLE_IMAGE_FILE = os.path.join(os.path.dirname(__file__), "example.png")
EXAMPLE_EVENTS_FILE = os.path.join(os.path.dirname(__file__), "example_events.ndjson")

REC_TOP_N   = 7    # 1. 最佳推荐数
REC_SHORT_N = 7    # 2. 常用词防守推荐数
REC_MULTI_N = 7    # 3. 多重组词推荐数 (一箭多雕)

MIN_DIST    = 3    # 走法间距

VIS_SHOW_DEBUG = True # Enable debug for web context

_easyocr_reader = None
_easyocr_reader_lock = threading.Lock()
_common_word_ranks = None


def get_easyocr_reader():
    """Load the heavy OCR model once per worker, on the first OCR stage."""
    global _easyocr_reader
    if _easyocr_reader is None:
        with _easyocr_reader_lock:
            if _easyocr_reader is None:
                _easyocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    return _easyocr_reader


def get_common_word_ranks():
    """Return Google-frequency rank for words that are valid in our dictionary."""
    global _common_word_ranks
    if _common_word_ranks is None:
        ranks = {}
        try:
            with open(COMMON_WORDS_FILE, 'r', encoding='utf-8') as handle:
                for rank, line in enumerate(handle):
                    word = line.strip().upper()
                    if len(word) > 1 and word not in ranks:
                        ranks[word] = rank
        except OSError:
            ranks = {}
        _common_word_ranks = ranks
    return _common_word_ranks

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
        self.opening_anchor = None

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
            opening_row, opening_col = self.opening_anchor or (self.rows // 2, self.cols // 2)
            if row == opening_row:
                anchors.append(opening_col)

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
        self.reader = None
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

    @staticmethod
    def _trim_template_background(template):
        """Remove the plain border around small UI templates before matching."""
        if template is None or template.size == 0:
            return template
        hsv = cv2.cvtColor(template, cv2.COLOR_BGR2HSV)
        saturation = hsv[:, :, 1]
        value = hsv[:, :, 2]
        mask = ((saturation > 35) & (value > 45)).astype(np.uint8) * 255
        points = cv2.findNonZero(mask)
        if points is None:
            return template
        x, y, w, h = cv2.boundingRect(points)
        pad = 2
        x1, y1 = max(0, x - pad), max(0, y - pad)
        x2 = min(template.shape[1], x + w + pad)
        y2 = min(template.shape[0], y + h + pad)
        return template[y1:y2, x1:x2]

    def _match_ui_anchor(self, gray, template_path, threshold=0.48):
        """Match a UI anchor at several scales and return its box in input pixels."""
        template = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if template is None:
            return {"found": False, "score": 0.0, "path": template_path}
        template = self._trim_template_background(template)
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        # Template matching cost grows quickly with screenshot resolution. Work
        # on a bounded image and map the winning box back to original pixels.
        image_scale = min(1.0, 1400.0 / gray.shape[1])
        if image_scale < 1.0:
            search_gray = cv2.resize(gray, None, fx=image_scale, fy=image_scale,
                                     interpolation=cv2.INTER_AREA)
        else:
            search_gray = gray

        best = None
        # Screenshots in the fixture set already vary in displayed game size.  A
        # multi-scale search makes the anchors independent of browser/Discord zoom.
        for scale in np.linspace(0.50, 1.40, 13):
            effective_scale = scale * image_scale
            width = max(12, int(template_gray.shape[1] * effective_scale))
            height = max(8, int(template_gray.shape[0] * effective_scale))
            if width >= search_gray.shape[1] or height >= search_gray.shape[0]:
                continue
            resized = cv2.resize(template_gray, (width, height), interpolation=cv2.INTER_AREA)
            result = cv2.matchTemplate(search_gray, resized, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(result)
            if best is None or score > best["score"]:
                best = {
                    "found": False,
                    "score": float(score),
                    "x": int(round(location[0] / image_scale)),
                    "y": int(round(location[1] / image_scale)),
                    "w": int(round(width / image_scale)),
                    "h": int(round(height / image_scale)),
                    "scale": float(scale),
                    "path": template_path,
                }

        if best is None:
            return {"found": False, "score": 0.0, "path": template_path}
        best["found"] = best["score"] >= threshold
        return best

    @staticmethod
    def _anchor_center(match):
        return match["x"] + match["w"] / 2.0, match["y"] + match["h"] / 2.0

    def _find_shuffle_control(self, img, logo, template_path):
        """Find the wide orange Shuffle control and reject square rack tiles."""
        if not logo.get("found"):
            return None

        lx, ly, lw, lh = logo["x"], logo["y"], logo["w"], logo["h"]
        ih, iw = img.shape[:2]
        # Once Logo is known, Shuffle can only occur in this lower control area.
        rx1 = max(0, int(lx + 1.80 * lw))
        ry1 = max(0, int(ly + 2.00 * lh))
        rx2 = min(iw, int(lx + 6.20 * lw))
        # Responsive/tall layouts can place the controls around 15 logo-heights
        # below the logo. Search to the bottom of the game viewport instead of
        # assuming the compact desktop geometry.
        ry2 = min(ih, int(ly + 20.0 * lh))
        if rx2 <= rx1 or ry2 <= ry1:
            return None

        roi = img[ry1:ry2, rx1:rx2]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        # Both controls and tiles are orange, but only controls form a wide,
        # shallow connected rectangle. Saturation/value thresholds tolerate
        # screenshot compression and Discord/browser scaling.
        # Saturation is the useful separator here: the board/background is a
        # pale peach (roughly S=60-85), while the control button is S>=150.
        mask = cv2.inRange(hsv, np.array([0, 120, 110]), np.array([24, 255, 255]))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        template = cv2.imread(template_path, cv2.IMREAD_COLOR)
        template = self._trim_template_background(template)
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY) if template is not None else None
        expected_x = lx + 3.95 * lw
        candidates = []

        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            x += rx1
            y += ry1
            aspect = w / max(1.0, float(h))
            if not (2.15 <= aspect <= 6.5):
                continue
            if not (0.28 * lw <= w <= 1.15 * lw and 0.20 * lh <= h <= 0.80 * lh):
                continue

            crop = img[y:y+h, x:x+w]
            text_score = 0.0
            if template_gray is not None and crop.size:
                crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                resized_tpl = cv2.resize(template_gray, (w, h), interpolation=cv2.INTER_AREA)
                text_score = float(cv2.matchTemplate(
                    crop_gray, resized_tpl, cv2.TM_CCOEFF_NORMED
                )[0, 0])

            position_error = abs(x - expected_x) / max(1.0, 1.5 * lw)
            geometry_score = max(0.0, 1.0 - position_error)
            combined = 0.75 * max(0.0, text_score) + 0.25 * geometry_score
            candidates.append({
                "found": True,
                "score": float(combined),
                "template_score": float(text_score),
                "geometry_score": float(geometry_score),
                "x": int(x), "y": int(y), "w": int(w), "h": int(h),
                "scale": float(w / max(1, template.shape[1])) if template is not None else 0.0,
                "path": template_path,
                "method": "orange_control",
                "search_box": [rx1, ry1, rx2, ry2],
            })

        if not candidates:
            return None
        best = max(candidates, key=lambda item: item["score"])
        # A weak candidate is safer to reject than to silently use a rack tile.
        if best["score"] < 0.30:
            best["found"] = False
        return best

    @staticmethod
    def _validate_template_shuffle(img, logo, candidate):
        """Reject a template hit on the orange rack even when its text looks similar."""
        if not logo.get("found") or not candidate.get("found"):
            return False
        expected_x = logo["x"] + 3.95 * logo["w"]
        if abs(candidate["x"] - expected_x) > 0.70 * logo["w"]:
            return False
        x, y, w, h = (candidate[k] for k in ("x", "y", "w", "h"))
        crop = img[max(0, y):max(0, y) + h, max(0, x):max(0, x) + w]
        if crop.size == 0:
            return False
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        orange = cv2.inRange(hsv, np.array([0, 120, 100]), np.array([24, 255, 255]))
        orange_ratio = float(np.count_nonzero(orange)) / orange.size
        candidate["orange_ratio"] = orange_ratio
        return orange_ratio >= 0.42

    def segment_image(self, img, logo_path, shuffle_path=SHUFFLE_IMAGE):
        h_img, w_img = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        logo = self._match_ui_anchor(gray, logo_path, threshold=0.45)
        raw_shuffle = self._match_ui_anchor(gray, shuffle_path, threshold=0.45)
        shuffle = self._find_shuffle_control(img, logo, shuffle_path)
        if not shuffle or not shuffle.get("found"):
            shuffle = raw_shuffle
            shuffle["method"] = "template_constrained"
            shuffle["found"] = self._validate_template_shuffle(img, logo, shuffle)

        mode = "fallback"
        if logo.get("found") and shuffle.get("found"):
            logo_cx, logo_cy = self._anchor_center(logo)
            shuffle_cx, shuffle_cy = self._anchor_center(shuffle)
            dx = shuffle_cx - logo_cx
            dy = shuffle_cy - logo_cy
            # Normalized geometry measured from the stable Letter League UI.
            # Using the distance between two anchors removes dependence on the
            # screenshot resolution and on the Discord/browser window size.
            if dx > 200 and dy > 150:
                # The game changes vertical spacing responsively. Interpolate
                # geometry using the anchor slope: compact/wide screenshots are
                # near 0.79, while tall browser layouts are near 1.49.
                anchor_ratio = dy / dx
                responsive_t = max(0.0, min(1.0, (anchor_ratio - 0.79) / 0.70))
                left_factor = -0.186 + responsive_t * 0.143
                top_factor = -0.127 + responsive_t * 0.273
                width_factor = 1.866 - responsive_t * 0.200
                height_factor = 1.302 - responsive_t * 0.512
                board_x1 = logo_cx + left_factor * dx
                board_y1 = logo_cy + top_factor * dy
                board_x2 = board_x1 + width_factor * dx
                board_y2 = board_y1 + height_factor * dy
                mode = "dual_anchor"
            else:
                logo["found"] = False

        if mode != "dual_anchor" and logo.get("found"):
            # Preserve a compatible single-anchor fallback for partially cropped
            # screenshots where the bottom controls are not visible.
            board_x1 = logo["x"]
            board_y1 = logo["y"] + logo["h"] * 0.10
            board_x2 = w_img * 0.95
            board_y2 = board_y1 + (board_x2 - board_x1) * 0.50
            mode = "logo_only"
        elif mode != "dual_anchor":
            board_x1, board_y1 = 0, h_img * 0.05
            board_x2, board_y2 = w_img, min(h_img, h_img * 0.75)

        def clamp_box(x1, y1, x2, y2):
            x1 = max(0, min(w_img - 1, int(round(x1))))
            y1 = max(0, min(h_img - 1, int(round(y1))))
            x2 = max(x1 + 1, min(w_img, int(round(x2))))
            y2 = max(y1 + 1, min(h_img, int(round(y2))))
            return x1, y1, x2, y2

        board_box = clamp_box(board_x1, board_y1, board_x2, board_y2)
        board = img[board_box[1]:board_box[3], board_box[0]:board_box[2]]

        if shuffle.get("found"):
            # The seven rack tiles sit directly below and around the Shuffle
            # control. Express the crop in units of the matched button size.
            sx, sy, sw, sh = shuffle["x"], shuffle["y"], shuffle["w"], shuffle["h"]
            rack_box = clamp_box(sx - 2.20 * sw, sy + 0.90 * sh,
                                 sx + 1.10 * sw, sy + 3.10 * sh)
        else:
            bw = board_box[2] - board_box[0]
            bh = board_box[3] - board_box[1]
            rack_box = clamp_box(board_box[0] + 0.37 * bw, board_box[1] + 0.86 * bh,
                                 board_box[0] + 0.70 * bw, board_box[1] + 0.99 * bh)
        rack = img[rack_box[1]:rack_box[3], rack_box[0]:rack_box[2]]

        overlay = img.copy()
        for match, color, label in ((logo, (0, 255, 0), "LOGO"),
                                    (shuffle, (255, 0, 255), "SHUFFLE")):
            if match.get("found"):
                p1 = (match["x"], match["y"])
                p2 = (match["x"] + match["w"], match["y"] + match["h"])
                cv2.rectangle(overlay, p1, p2, color, 3)
                cv2.putText(overlay, f"{label} {match['score']:.2f}",
                            (p1[0], max(20, p1[1] - 8)), cv2.FONT_HERSHEY_SIMPLEX,
                            0.65, color, 2)
        cv2.rectangle(overlay, board_box[:2], board_box[2:], (255, 255, 0), 3)
        cv2.rectangle(overlay, rack_box[:2], rack_box[2:], (0, 165, 255), 3)

        self.debug_info['logo_detection'] = logo
        self.debug_info['shuffle_detection'] = shuffle
        self.debug_info['layout_detection'] = {
            'mode': mode,
            'board_box': list(board_box),
            'rack_box': list(rack_box),
        }
        if mode == "dual_anchor":
            self.debug_info['layout_detection']['anchor_ratio'] = float(dy / dx)
        self.debug_info['layout_overlay'] = self._img_to_base64(overlay)
        return board, rack

    def _detect_rack_tiles(self, img):
        """Detect the fixed row of seven square rack tiles at any UI scale."""
        height, width = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        binary = cv2.adaptiveThreshold(
            enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2,
        )
        binary = cv2.erode(binary, np.ones((3, 3), np.uint8), iterations=1)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            ratio = w / max(1.0, float(h))
            if (0.30 * height <= w <= 0.90 * height and
                    0.30 * height <= h <= 0.90 * height and
                    0.72 <= ratio <= 1.30):
                candidates.append((x, y, w, h))

        # Find the most coherent horizontal row. This rejects the button row
        # without relying on absolute pixel sizes.
        best_row = []
        for seed in candidates:
            seed_cy = seed[1] + seed[3] / 2.0
            row = [box for box in candidates
                   if abs((box[1] + box[3] / 2.0) - seed_cy) <= 0.14 * height]
            score = (-abs(len(row) - 7), sum(box[2] * box[3] for box in row))
            best_score = (-abs(len(best_row) - 7),
                          sum(box[2] * box[3] for box in best_row))
            if score > best_score:
                best_row = row

        if best_row:
            median_area = float(np.median([box[2] * box[3] for box in best_row]))
            best_row = [box for box in best_row
                        if 0.60 <= (box[2] * box[3]) / median_area <= 1.45]
        best_row.sort(key=lambda box: box[0])

        deduped = []
        for box in best_row:
            if deduped and abs(box[0] - deduped[-1][0]) < 0.20 * box[2]:
                if box[2] * box[3] > deduped[-1][2] * deduped[-1][3]:
                    deduped[-1] = box
            else:
                deduped.append(box)
        if len(deduped) > 7:
            # Seven consecutive boxes with the most regular spacing wins.
            windows = [deduped[i:i + 7] for i in range(len(deduped) - 6)]
            deduped = min(windows, key=lambda boxes: np.std([
                boxes[i + 1][0] - boxes[i][0] for i in range(6)
            ]))

        debug = img.copy()
        for index, (x, y, w, h) in enumerate(deduped):
            cv2.rectangle(debug, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(debug, str(index + 1), (x + 2, max(12, y - 3)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
        self.debug_info['rack_binary_map'] = self._img_to_base64(binary)
        self.debug_info['rack_contour_debug'] = self._img_to_base64(debug)
        self.debug_info['rack_binary_contour_debug'] = self._img_to_base64(
            cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
        )
        return deduped

    def _rack_glyph(self, tile):
        """Extract the large white glyph while masking the small score digit."""
        h, w = tile.shape[:2]
        hsv = cv2.cvtColor(tile, cv2.COLOR_BGR2HSV)
        white = cv2.inRange(hsv, np.array([0, 0, 175]), np.array([180, 115, 255]))
        # Remove rounded tile border and the score in the top-right corner.
        border_x, border_y = max(1, int(w * 0.08)), max(1, int(h * 0.08))
        white[:border_y, :] = 0
        white[-border_y:, :] = 0
        white[:, :border_x] = 0
        white[:, -border_x:] = 0
        white[:int(h * 0.24), int(w * 0.72):] = 0

        contours, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = [c for c in contours if cv2.contourArea(c) >= max(2, 0.006 * w * h)]
        if not contours:
            return None, white, None
        contour = max(contours, key=cv2.contourArea)
        x, y, gw, gh = cv2.boundingRect(contour)
        # A score fragment is small and lives high/right; it is not a rack glyph.
        if gh < 0.28 * h or gw < 2:
            return None, white, None
        glyph = white[y:y + gh, x:x + gw]
        return glyph, white, (x, y, gw, gh)

    @staticmethod
    def _normalize_rack_glyph(glyph):
        canvas = np.ones((96, 96), dtype=np.uint8) * 255
        gh, gw = glyph.shape[:2]
        scale = min(60.0 / max(1, gw), 68.0 / max(1, gh))
        resized = cv2.resize(glyph, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_NEAREST)
        rh, rw = resized.shape[:2]
        y, x = (96 - rh) // 2, (96 - rw) // 2
        target = canvas[y:y + rh, x:x + rw]
        target[resized > 0] = 0
        return canvas

    def _recognize_rack_tile(self, tile):
        glyph, glyph_mask, bbox = self._rack_glyph(tile)
        if glyph is None:
            return '?', 1.0, '没有主字形，判为万能牌', glyph_mask, None

        canvas = self._normalize_rack_glyph(glyph)
        gh, gw = glyph.shape[:2]
        ratio = gw / max(1.0, float(gh))
        # I is uniquely narrow in this tile font and is often missed entirely
        # by general-purpose text detectors.
        if ratio < 0.43:
            return 'I', 1.0, f'窄高字形({ratio:.2f})', glyph_mask, canvas

        normalized = self.reader.recognize(
            canvas, horizontal_list=[(0, 96, 0, 96)], free_list=[],
            detail=1, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ',
        )
        norm_char, norm_conf = '', 0.0
        if normalized:
            norm_char = (normalized[0][1] or '').strip().upper()[:1]
            norm_conf = float(normalized[0][2] or 0.0)

        # Keep a color-image candidate for shapes such as Z/N where EasyOCR's
        # normalized recognizer can be overconfident about a similar glyph.
        color_char, color_conf = '', 0.0
        if norm_char in ('', 'M', 'T', 'W'):
            color_roi = tile[int(tile.shape[0] * 0.16):int(tile.shape[0] * 0.92),
                             int(tile.shape[1] * 0.10):int(tile.shape[1] * 0.86)]
            color_roi = cv2.copyMakeBorder(color_roi, 12, 12, 12, 12,
                                           cv2.BORDER_CONSTANT, value=(255, 255, 255))
            color_roi = cv2.resize(color_roi, None, fx=3, fy=3,
                                   interpolation=cv2.INTER_CUBIC)
            color_result = self.reader.readtext(
                color_roi, detail=1, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ'
            )
            if color_result:
                best = max(color_result, key=lambda item: float(item[2]))
                color_char = (best[1] or '').strip().upper()[:1]
                color_conf = float(best[2] or 0.0)

        char, confidence, reason = norm_char, norm_conf, '标准化字形'
        if not char and color_char:
            char, confidence, reason = color_char, color_conf, '彩色原图兜底'
        elif norm_char == 'M' and color_char == 'N':
            char, confidence, reason = 'N', max(norm_conf, color_conf), 'N/M 双通道修正'
        elif norm_char == 'T' and color_char == 'Z':
            char, confidence, reason = 'Z', max(norm_conf, color_conf), 'Z/T 双通道修正'

        # U has two nearly vertical outer strokes and a sparse centre column;
        # EasyOCR commonly calls this rounded game font W at small sizes.
        binary = glyph > 0
        center_fill = float(binary[:, binary.shape[1] // 2].mean())
        edge_fill = float((binary[:, 0].mean() + binary[:, -1].mean()) / 2.0)
        quarter = max(1, binary.shape[0] // 4)
        top_fill = float(binary[:quarter].mean())
        bottom_fill = float(binary[-quarter:].mean())
        if char == 'T' and top_fill < 0.60 and bottom_fill > 0.42:
            char, reason = 'Z', 'Z/T 底部横笔结构修正'
        if char == 'W' and edge_fill > 0.55 and center_fill < 0.45:
            char, reason = 'U', 'U/W 字形结构修正'

        if not char:
            char, confidence, reason = '?', 0.0, '双通道均未识别'
        return char, confidence, reason, glyph_mask, canvas

    def ocr_rack(self, img):
        if self.reader is None:
            self.reader = get_easyocr_reader()
        boxes = self._detect_rack_tiles(img)
        results = []
        details = [{
            'index': -1,
            'raw': f'检测到 {len(boxes)} 个牌块（目标 7）',
            'final': 'STATS',
            'process': '自适应方块检测 + 固定七槽单字识别',
            'steps': [],
        }]
        glyph_cards = []
        for index, (x, y, w, h) in enumerate(boxes):
            tile = img[y:y + h, x:x + w]
            char, confidence, reason, glyph_mask, canvas = self._recognize_rack_tile(tile)
            results.append(char)
            steps = [{
                'name': '主字母蒙版',
                'image': self._img_to_base64(glyph_mask),
                'result': char,
                'success': char != '?',
            }]
            if canvas is not None:
                steps.append({
                    'name': '标准化单字',
                    'image': self._img_to_base64(canvas),
                    'result': f'{char} ({confidence:.2f})',
                    'success': char != '?',
                })
            details.append({
                'index': index,
                'raw': f'confidence={confidence:.3f}',
                'final': char,
                'process': reason,
                'steps': steps,
            })
            glyph_cards.append((tile, char, confidence))

        self.debug_info['rack_debug_data'] = details
        recognition_debug = img.copy()
        for index, ((x, y, w, h), char) in enumerate(zip(boxes, results)):
            color = (0, 200, 0) if char != '?' else (0, 165, 255)
            cv2.rectangle(recognition_debug, (x, y), (x + w, y + h), color, 2)
            cv2.putText(recognition_debug, char, (x + max(2, w // 3), y + h - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, max(0.45, h / 70.0), color, 2)
        # Keep the transport key for frontend compatibility; its content is now
        # the useful final rack recognition rather than another contour bitmap.
        self.debug_info['rack_binary_contour_debug'] = self._img_to_base64(recognition_debug)
        self.debug_info['rack_y_filter'] = {
            'reason': f'固定横排检测：{len(boxes)}/7',
            'before_count': len(boxes), 'after_count': len(boxes),
        }
        self.debug_info['rack_area_filter'] = {
            'reason': '尺寸阈值按字母架高度自适应',
            'before_count': len(boxes), 'after_count': len(boxes),
        }
        return results

    def _ocr_rack_legacy(self, img):
        if self.reader is None:
            self.reader = get_easyocr_reader()
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
        if self.reader is None:
            self.reader = get_easyocr_reader()
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

        # --- Grid fitting from the board itself (works even when it is empty) ---
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        white_lines = ((hsv[:, :, 1] < 45) & (hsv[:, :, 2] > 225)).astype(np.uint8)

        def line_centers(profile, threshold):
            indexes = np.where(profile > threshold)[0]
            groups = []
            for value in indexes:
                if not groups or value > groups[-1][-1] + 1:
                    groups.append([int(value)])
                else:
                    groups[-1].append(int(value))
            centers = [float(np.mean(group)) for group in groups]
            merged = []
            for center in centers:
                if merged and center - merged[-1] < 5:
                    merged[-1] = (merged[-1] + center) / 2.0
                else:
                    merged.append(center)
            return merged

        vertical = line_centers(white_lines.mean(axis=0), 0.35)
        horizontal = line_centers(white_lines.mean(axis=1), 0.35)
        if len(vertical) < 5 or len(horizontal) < 5:
            # Retain a controlled fallback for unusually cropped screenshots.
            step = float(np.median([d for d in np.diff(vertical) if 10 < d < 150])) if len(vertical) > 2 else w / 15.0
            vertical = [i * step for i in range(int(w / step) + 1)]
            horizontal = [i * step for i in range(int(h / step) + 1)]

        true_step_x = float(np.median([d for d in np.diff(vertical) if 10 < d < 150]))
        true_step_y = float(np.median([d for d in np.diff(horizontal) if 10 < d < 150]))
        grid_origin_x = float(vertical[0] + true_step_x / 2.0)
        grid_origin_y = float(horizontal[0] + true_step_y / 2.0)
        cols, rows = len(vertical) - 1, len(horizontal) - 1
        matrix = [['' for _ in range(cols)] for _ in range(rows)]
        debug_viz = img.copy()

        for x in vertical:
            cv2.line(debug_viz, (int(x), 0), (int(x), h), (255, 255, 0), 1)
        for y in horizontal:
            cv2.line(debug_viz, (0, int(y)), (w, int(y)), (255, 255, 0), 1)
        for item in detected_raw:
            char = item.get('char', '')
            if not (len(char) == 1 and char.upper() in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
                continue
            c_idx = int(round((item['cx'] - grid_origin_x) / true_step_x))
            r_idx = int(round((item['cy'] - grid_origin_y) / true_step_y))
            if 0 <= r_idx < rows and 0 <= c_idx < cols:
                matrix[r_idx][c_idx] = char
                cv2.putText(debug_viz, char, (int(item['cx']) - 5, int(item['cy']) + 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)

        # UI labels occasionally resemble an isolated tile near the board edge.
        # Every legal board tile belongs to an orthogonally connected word, so
        # discard singleton OCR detections without touching real words.
        for r in range(rows):
            for c in range(cols):
                if not matrix[r][c]:
                    continue
                neighbors = ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1))
                if not any(0 <= rr < rows and 0 <= cc < cols and matrix[rr][cc]
                           for rr, cc in neighbors):
                    matrix[r][c] = ''

        self.debug_info['grid_fit'] = self._img_to_base64(debug_viz)
        self.debug_info['grid_params'] = {
            'step_x': true_step_x, 'step_y': true_step_y,
            'origin_x': grid_origin_x, 'origin_y': grid_origin_y,
            'rows': rows, 'cols': cols,
        }
        self.grid_params = (grid_origin_x, grid_origin_y, true_step_x, true_step_y, rows, cols)
        print(f"📏 网格线拟合: {cols}x{rows}, {true_step_x:.1f}x{true_step_y:.1f}")
        return matrix

    def detect_premium_cells(self, board_matrix):
        """Classify empty 2L/3L/2W/3W cells from their stable background color."""
        rows = len(board_matrix)
        cols = len(board_matrix[0]) if rows else 0
        premiums = [['' for _ in range(cols)] for _ in range(rows)]
        if not self.grid_params or self.seg_board_img is None:
            return premiums
        ox, oy, sx, sy, _, _ = self.grid_params
        # BGR samples from the Letter League board. Color distance is more
        # stable than OCR because the label can move to the corner under a tile.
        prototypes = {
            '2L': np.array([242, 199, 114], dtype=np.float32),
            '2W': np.array([166, 242, 174], dtype=np.float32),
            '3L': np.array([44, 172, 242], dtype=np.float32),
            '3W': np.array([80, 124, 230], dtype=np.float32),
        }
        image = self.seg_board_img
        ih, iw = image.shape[:2]
        opening_anchor = None
        opening_strength = 0
        for r in range(rows):
            for c in range(cols):
                if board_matrix[r][c]:
                    continue
                cx, cy = int(round(ox + c * sx)), int(round(oy + r * sy))
                radius = max(2, int(min(sx, sy) * 0.22))
                x1, x2 = max(0, cx - radius), min(iw, cx + radius + 1)
                y1, y2 = max(0, cy - radius), min(ih, cy + radius + 1)
                if x2 <= x1 or y2 <= y1:
                    continue
                color = np.median(image[y1:y2, x1:x2].reshape(-1, 3), axis=0)
                name, distance = min(
                    ((name, float(np.linalg.norm(color - sample)))
                     for name, sample in prototypes.items()),
                    key=lambda item: item[1],
                )
                if distance < 75.0:
                    premiums[r][c] = name
                patch = image[y1:y2, x1:x2]
                patch_hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
                purple = cv2.inRange(patch_hsv, np.array([125, 80, 100]),
                                     np.array([165, 255, 255]))
                purple_count = int(np.count_nonzero(purple))
                if (1 < r < rows - 2 and 1 < c < cols - 2 and
                        purple_count > max(3, purple.size * 0.008) and
                        purple_count > opening_strength):
                    opening_anchor = (r, c)
                    opening_strength = purple_count
        self.debug_info['premium_matrix'] = premiums
        self.debug_info['opening_anchor'] = opening_anchor
        return premiums

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


# Letter League values observed on the rack tiles. Lowercase move letters are
# blank tiles and deliberately score zero.
LETTER_SCORES = {
    'A': 1, 'B': 3, 'C': 3, 'D': 2, 'E': 1, 'F': 4, 'G': 2,
    'H': 4, 'I': 1, 'J': 8, 'K': 5, 'L': 2, 'M': 3, 'N': 1,
    'O': 1, 'P': 3, 'Q': 10, 'R': 1, 'S': 1, 'T': 1, 'U': 1,
    'V': 4, 'W': 4, 'X': 8, 'Y': 4, 'Z': 10,
}


def _tile_score(char):
    return 0 if char.islower() else LETTER_SCORES.get(char.upper(), 0)


def score_move(move, board, premiums):
    """Score a move, including every newly formed perpendicular word."""
    row, col, word = move['row'], move['col'], move['word']
    vertical = move.get('direction') == 'V'
    dr, dc = (1, 0) if vertical else (0, 1)
    cross_dr, cross_dc = (0, 1) if vertical else (1, 0)
    rows = len(board)
    cols = len(board[0]) if rows else 0
    main_points = 0
    main_word_multiplier = 1
    cross_points = 0
    placed = []

    for index, char in enumerate(word):
        rr, c = row + index * dr, col + index * dc
        if not (0 <= rr < rows and 0 <= c < cols):
            continue
        existing = board[rr][c]
        if existing:
            main_points += _tile_score(existing)
            continue
        premium = premiums[rr][c] if premiums else ''
        letter_multiplier = 3 if premium == '3L' else 2 if premium == '2L' else 1
        word_multiplier = 3 if premium == '3W' else 2 if premium == '2W' else 1
        points = _tile_score(char) * letter_multiplier
        main_points += points
        main_word_multiplier *= word_multiplier
        placed.append({
            'row': rr, 'col': c, 'letter': char.upper(),
            'base': _tile_score(char), 'premium': premium,
            'points': points,
        })

        before_r, before_c = rr - cross_dr, c - cross_dc
        before_points = 0
        before_count = 0
        while 0 <= before_r < rows and 0 <= before_c < cols and board[before_r][before_c]:
            before_points += _tile_score(board[before_r][before_c])
            before_count += 1
            before_r -= cross_dr
            before_c -= cross_dc
        after_r, after_c = rr + cross_dr, c + cross_dc
        after_points = 0
        after_count = 0
        while 0 <= after_r < rows and 0 <= after_c < cols and board[after_r][after_c]:
            after_points += _tile_score(board[after_r][after_c])
            after_count += 1
            after_r += cross_dr
            after_c += cross_dc
        if before_count or after_count:
            subtotal = points
            subtotal += before_points + after_points
            cross_points += subtotal * word_multiplier

    main_total = main_points * main_word_multiplier
    move['score'] = int(main_total + cross_points)
    move['score_breakdown'] = {
        'main': int(main_total), 'cross': int(cross_points),
        'word_multiplier': int(main_word_multiplier), 'placed': placed,
    }
    return move['score']

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


@bp.route("/api/letter/example/image")
def letter_example_image():
    """Return the source screenshot used by the interactive example."""
    return send_file(EXAMPLE_IMAGE_FILE, mimetype="image/png", max_age=3600)


@bp.route("/api/letter/example")
def letter_example():
    """Replay precomputed example events through the normal streaming UI."""
    if not os.path.exists(EXAMPLE_EVENTS_FILE):
        return jsonify({"status": "error", "message": "Example data is unavailable"}), 404

    def generate():
        with open(EXAMPLE_EVENTS_FILE, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                yield line if line.endswith("\n") else line + "\n"
                # Make the cached run readable while preserving the same
                # progressive behaviour as a real calculation.
                time.sleep(0.16)

    response = Response(generate(), mimetype="application/x-ndjson")
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Content-Encoding'] = 'identity'
    return response

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
            debug_data['shuffle_detection'] = vision.debug_info.get('shuffle_detection')
            debug_data['layout_detection'] = vision.debug_info.get('layout_detection')
            debug_data['layout_overlay'] = vision.debug_info.get('layout_overlay')
            
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
            premium_matrix = vision.detect_premium_cells(board_matrix)
            
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
                "grid_params": grid_params,
                "premium_matrix": premium_matrix
            }}) + "\n"

            # 4. Solving
            yield json.dumps({"type": "step", "msg": "正在计算最佳走法..."}) + "\n"
            solver = ScrabbleSolver(get_gaddag())
            solver.set_board(board_matrix)
            solver.opening_anchor = vision.debug_info.get('opening_anchor')
            
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
            for move in moves:
                move['direction'] = 'H'

            transposed_board = [list(row) for row in zip(*board_matrix)]
            vertical_solver = ScrabbleSolver(get_gaddag())
            vertical_solver.set_board(transposed_board)
            if solver.opening_anchor:
                vertical_solver.opening_anchor = (solver.opening_anchor[1], solver.opening_anchor[0])
            vertical_moves = vertical_solver.solve(rack_str)
            for move in vertical_moves:
                original_row, original_col = move['col'], move['row']
                move['row'], move['col'] = original_row, original_col
                move['direction'] = 'V'
            moves.extend(vertical_moves)
            for move in moves:
                score_move(move, board_matrix, premium_matrix)

            def public_move(move):
                return {
                    "word": move["word"], "row": move["row"], "col": move["col"],
                    "direction": move.get("direction", "H"),
                    "cross": move.get("cross", 0), "score": move.get("score", 0),
                    "score_breakdown": move.get("score_breakdown", {}),
                }
            
            # 5. Formatting Results
            results = {
                "best": [],
                "short": [],
                "multi": [],
                "highest": [],
                "result_image": None,
                "board_image": vision._img_to_base64(vision.seg_board_img),
                "board_matrix": board_matrix,
                "premium_matrix": premium_matrix,
                "grid_params": grid_params,
            }
            
            final_viz_list = []
            if moves:
                # 1. Top Best
                diverse_top = get_diverse_moves(moves, top_n=rec_top_n, min_dist=min_dist)
                for m in diverse_top:
                    m['type'] = 'best'
                    final_viz_list.append(m)
                    results["best"].append(public_move(m))

                # 2. Common-word defense. This is based on Google frequency,
                # not word length, so useful words such as GOOD and GRAMMAR are
                # eligible alongside shorter everyday words.
                common_ranks = get_common_word_ranks()
                short_moves = [m for m in moves if m['word'].upper() in common_ranks]
                short_moves.sort(key=lambda m: (
                    common_ranks[m['word'].upper()],
                    -m.get('score', 0),
                    -len(m['word']),
                ))
                diverse_short = get_diverse_moves(short_moves, top_n=rec_short_n, min_dist=min_dist)
                for m in diverse_short:
                    m['type'] = 'short'
                    final_viz_list.append(m)
                    results["short"].append(public_move(m))

                # 3. Top Multi
                multi_moves = [m for m in moves if m['cross'] > 0]
                multi_moves.sort(key=lambda x: x['cross'], reverse=True)
                diverse_multi = get_diverse_moves(multi_moves, top_n=rec_multi_n, min_dist=min_dist)
                for m in diverse_multi:
                    m['type'] = 'multi'
                    if m not in final_viz_list:
                        final_viz_list.append(m)
                    results["multi"].append(public_move(m))

                # 4. Highest scoring placements. Diversity is applied after
                # actual board/premium/cross-word scoring.
                scored_moves = sorted(moves, key=lambda item: item.get('score', 0), reverse=True)
                highest = get_diverse_moves(scored_moves, top_n=rec_top_n, min_dist=min_dist)
                results["highest"] = [public_move(move) for move in highest]

            yield json.dumps({"type": "result", "data": results}) + "\n"
            yield json.dumps({"type": "step", "msg": "完成!"}) + "\n"

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    response = Response(generate(), mimetype='application/x-ndjson')
    # Force every yielded NDJSON line through Gunicorn/Nginx immediately.
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Content-Encoding'] = 'identity'
    return response
