import json
import re
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

# ========= 1. 自动加载中文字体（避免 ttc 解析崩溃） =========
zh_font = None
for f in font_manager.findSystemFonts():
    if any(k in f.lower() for k in ["wqy", "noto", "msyh", "simhei", "simkai", "uming", "ukai"]):
        zh_font = f
        break

if zh_font:
    myfont = font_manager.FontProperties(fname=zh_font)
    print("Using font file:", zh_font)
else:
    myfont = None
    print("WARNING: No Chinese font found")

plt.rcParams["axes.unicode_minus"] = False

# ========= 2. 读取 JSON =========
with open("/home/bbdwz/projects/website/map/output/response.json", "r", encoding="utf-8") as f:
    data = json.load(f)

steps = data["result"]["routes"][0]["steps"]

# ========= 3. 构造可变宽度柱状图数据 =========
left_positions = []   # 每个柱子的起始 x（km）
widths = []           # 每个柱子的宽度（km）
heights = []          # 映射后的高度（%）
labels = []           # 道路名

current_x = 0.0

for step in steps:
    distance_km = step["distance"] / 1000.0
    road_type = int(step.get("road_type", 9))

    # 映射：0 -> 100%, 9 -> 50%
    height = 100 - (road_type / 9.0) * 50

    instruction = step["instruction"]
    roads = re.findall(r"<b>(.*?)</b>", instruction)
    road_name = roads[0] if roads else "未知路段"

    left_positions.append(current_x)
    widths.append(distance_km)
    heights.append(height)
    labels.append(road_name)

    current_x += distance_km

# ========= 4. 高度 → 颜色映射 =========
norm = plt.Normalize(min(heights), max(heights))
cmap = plt.cm.viridis
colors = cmap(norm(heights))

# ========= 5. 绘图 =========
plt.figure(figsize=(16, 5))

plt.bar(
    left_positions,
    heights,
    width=widths,
    align="edge",
    color=colors
)

plt.xlabel("Distance (km)", fontproperties=myfont)
plt.ylabel("Road Type Mapping (%)", fontproperties=myfont)
plt.title("Route Segments by Distance and Road Type", fontproperties=myfont)

# 在每个柱子底部标注道路名
for left, w, name in zip(left_positions, widths, labels):
    center = left + w / 2
    plt.text(
        center, 3, name,
        rotation=90,
        ha="center",
        va="bottom",
        fontsize=8,
        fontproperties=myfont
    )

# 颜色条
sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
sm.set_array([])
plt.colorbar(sm, label="Mapped Road Type (%)")

plt.tight_layout()

# ========= 6. 保存图片 =========
plt.savefig("route_bar.png", dpi=150)
print("Saved to route_bar.png")