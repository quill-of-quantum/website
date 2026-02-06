#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
固定区域 + 固定折线的匹配逻辑验证脚本。

运行方式:
  python3 /home/bbdwz/projects/website/route_creator/test.py

说明:
- 使用 route_creator_api._run_match 直接执行匹配流程。
- 区域为 bbox，小范围以避免 area too large。
- 手绘点为 (x, y) 归一化坐标，三段折线。
"""

import sys
import types
import json
from pathlib import Path

try:
    import flask  # noqa: F401
except Exception:
    class _BlueprintStub:
        def __init__(self, *args, **kwargs):
            pass
        def route(self, *args, **kwargs):
            def _decorator(fn):
                return fn
            return _decorator

    flask_stub = types.SimpleNamespace(
        Blueprint=_BlueprintStub,
        request=None,
        jsonify=lambda *args, **kwargs: None,
    )
    sys.modules.setdefault("flask", flask_stub)

from route_creator_api import _run_match, PROGRESS, RESULTS


def main():
    # 固定区域 (Munich 中心附近的小矩形)
    area = {
        "type": "bbox",
        "north": 48.1470,
        "south": 48.1340,
        "east": 11.5900,
        "west": 11.5650
    }

    # 固定折线 (归一化坐标, 0~1)
    sketch = [
        {"x": 0.15, "y": 0.20},
        {"x": 0.45, "y": 0.55},
        {"x": 0.75, "y": 0.30},
        {"x": 0.90, "y": 0.60}
    ]

    request_id = "test_fixed_area_polyline"
    top_k = 3

    print("=== Running match with fixed area + polyline ===")
    _run_match(request_id, area, sketch, top_k)

    print("\n=== Progress ===")
    for k, v in PROGRESS.items():
        if k == request_id:
            print(v)

    print("\n=== Result (summary) ===")
    result = RESULTS.get(request_id)
    if not result:
        print("No result found.")
        return
    if not result.get("ok"):
        print("Match failed:", result.get("error"))
        return

    debug = result.get("debug", {})
    print("debug:", debug)
    print("candidates:", len(result.get("candidates", [])))
    for i, c in enumerate(result.get("candidates", []), 1):
        print(f"#{i} score={c.get('score')} length_km={c.get('length_m', 0) / 1000:.2f}")

    # Output: save candidate routes as an image for quick visual check.
    out_dir = Path(__file__).resolve().parent
    img_path = out_dir / "match_result.png"
    geojson_path = out_dir / "match_result.geojson"

    candidates = result.get("candidates", [])
    if not candidates:
        print("No candidates to render.")
        return

    # Save GeoJSON as a fallback / reference.
    features = []
    for i, c in enumerate(candidates, 1):
        coords = c.get("coords", [])
        features.append({
            "type": "Feature",
            "properties": {
                "rank": i,
                "score": c.get("score"),
                "length_m": c.get("length_m")
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[lng, lat] for lat, lng in coords]
            }
        })
    geojson = {"type": "FeatureCollection", "features": features}
    geojson_path.write_text(json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"matplotlib not available: {e}")
        print(f"GeoJSON saved to: {geojson_path}")
        return

    plt.figure(figsize=(7, 7), dpi=160)
    for i, c in enumerate(candidates, 1):
        coords = c.get("coords", [])
        if not coords:
            continue
        lats = [p[0] for p in coords]
        lngs = [p[1] for p in coords]
        if i == 1:
            plt.plot(lngs, lats, color="#ff4d4f", linewidth=2.5, label="best")
        else:
            plt.plot(lngs, lats, color="#555555", linewidth=1.5, linestyle="--")
    plt.title("Matched Route Candidates")
    plt.xlabel("Longitude")
    plt.ylabel("Latitude")
    plt.axis("equal")
    plt.grid(True, linestyle=":", alpha=0.4)
    plt.tight_layout()
    plt.savefig(img_path)
    print(f"Saved image: {img_path}")
    print(f"Saved GeoJSON: {geojson_path}")


if __name__ == "__main__":
    main()
