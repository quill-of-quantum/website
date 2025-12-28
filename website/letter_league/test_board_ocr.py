import cv2, numpy as np
from paddleocr import PaddleOCR

ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)

img = cv2.imread("./output/board.png")
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

N = 5   # 棋盘尺寸
h,w = gray.shape
cell_h = h//N
cell_w = w//N

for r in range(N):
    row=[]
    for c in range(N):
        roi = img[r*cell_h:(r+1)*cell_h, c*cell_w:(c+1)*cell_w]
        result = ocr.ocr(roi, cls=False)
        ch=""
        if result and result[0]:
            ch = result[0][0][1][0]
        row.append(ch)
    print(row)