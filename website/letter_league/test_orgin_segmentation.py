import cv2, os
import numpy as np

IMG = "./test.png"
LOGO_L = "./logo_l.png"
OUT = "./output"
os.makedirs(OUT, exist_ok=True)

img = cv2.imread(IMG)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
tpl_l = cv2.imread(LOGO_L, 0)

def match(tpl):
    res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
    _, maxv, _, maxloc = cv2.minMaxLoc(res)
    h, w = tpl.shape
    return maxloc[0], maxloc[1], w, h, float(maxv)

lx, ly, lw, lh, lv = match(tpl_l)
print("match score logo_l:", lv, "at", (lx, ly), "size", (lw, lh))

H, W = img.shape[:2]

# ========== 新逻辑：单锚点比例法 ==========
game_left = lx
game_width = W - game_left

# 顶部从 logo 底部向下
TOP_RATIO = 0.10       # 0.12~0.18 之间微调
game_top = int(ly + lh * TOP_RATIO)

# 高度仍然按宽度比例
GAME_H_RATIO = 0.5
game_height = int(game_width * GAME_H_RATIO)
game_bottom = min(H, game_top + game_height)
game_right = W

game_w = game_right - game_left
game_h = game_bottom - game_top

# ========== 内部分区比例 ==========
BOARD_X0 = 0.00   # 左侧偏移比例
BOARD_Y0 = 0.13
BOARD_W  = 0.9   # 宽度比例
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
    return img[y:y+h, x:x+w]

board = crop_ratio(BOARD_X0, BOARD_Y0, BOARD_W, BOARD_H)
rack  = crop_ratio(RACK_X0,  RACK_Y0,  RACK_W,  RACK_H)

cv2.imwrite(os.path.join(OUT,"board.png"), board)
cv2.imwrite(os.path.join(OUT,"rack.png"),  rack)

# ========== debug ==========
dbg = img.copy()

# logo 蓝框
cv2.rectangle(dbg,(lx,ly),(lx+lw,ly+lh),(255,0,0),2)

# game 绿框
cv2.rectangle(dbg,(game_left,game_top),(game_right,game_bottom),(0,255,0),3)

# board / rack 红框
bx = int(game_left + BOARD_X0 * game_w)
by = int(game_top  + BOARD_Y0 * game_h)
bw = int(BOARD_W   * game_w)
bh = int(BOARD_H   * game_h)

rx = int(game_left + RACK_X0  * game_w)
ry = int(game_top  + RACK_Y0  * game_h)
rw = int(RACK_W    * game_w)
rh = int(RACK_H    * game_h)

cv2.rectangle(dbg,(bx,by),(bx+bw,by+bh),(0,0,255),2)
cv2.rectangle(dbg,(rx,ry),(rx+rw,ry+rh),(0,0,255),2)

cv2.imwrite(os.path.join(OUT,"debug_overlay.png"), dbg)

print("完成：./output/ 下生成 board.png / rack.png / debug_overlay.png")