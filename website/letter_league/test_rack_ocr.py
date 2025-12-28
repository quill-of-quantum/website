import cv2, numpy as np
from paddleocr import PaddleOCR

ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)

img = cv2.imread("./output/rack.png")
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
gray = cv2.equalizeHist(gray)
_, th = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY_INV)

cnts,_ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

tiles=[]
for c in cnts:
    x,y,w,h = cv2.boundingRect(c)
    if 40<w<120 and 40<h<120:
        tiles.append((x,y,w,h))

tiles = sorted(tiles, key=lambda b:b[0])

for i,(x,y,w,h) in enumerate(tiles):
    roi = img[y:y+h, x:x+w]
    result = ocr.ocr(roi, cls=False)
    ch = ""
    if result and result[0]:
        ch = result[0][0][1][0]
    print(i, ch)