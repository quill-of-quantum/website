#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
固定区域 + 固定折线的匹配逻辑验证脚本。

运行方式:
  python3 /home/bbdwz/projects/website/tests/route_creator/test.py

说明:
- 使用 modules.route_creator.api 直接执行匹配流程。
- 区域为 bbox，小范围以避免 area too large。
- 手绘点为 (x, y) 归一化坐标，三段折线。
"""

import sys
import types
import json
import math
import threading
import time
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

from modules.route_creator import api as rca
from modules.route_creator.api import PROGRESS, RESULTS


def _run_match_local_only(request_id, area, sketch, top_k):
    try:
        import osmnx as ox
        import networkx as nx
        from pyrosm import OSM
    except Exception as e:
        PROGRESS[request_id] = {"status": "error", "detail": f"missing deps: {e}"}
        RESULTS[request_id] = {"ok": False, "error": f"missing deps: {e}"}
        return

    def _set_progress(status, detail, percent=None):
        payload = {"status": status, "detail": detail}
        if percent is not None:
            payload["percent"] = int(percent)
        PROGRESS[request_id] = payload

    _set_progress("fetch", f"local pbf {rca.LOCAL_OSM_PBF_PATH}", 12)

    north, south, east, west = area["north"], area["south"], area["east"], area["west"]
    try:
        # Pass bounding box to pyrosm to avoid loading the entire PBF
        osm = OSM(rca.LOCAL_OSM_PBF_PATH, bounding_box=[west, south, east, north])
        net = osm.get_network(network_type="driving", nodes=True)
    except Exception as e:
        _set_progress("error", f"local pbf failed: {e}")
        RESULTS[request_id] = {"ok": False, "error": f"local pbf failed: {e}"}
        return

    if net is None:
        _set_progress("error", "local pbf failed: empty graph")
        RESULTS[request_id] = {"ok": False, "error": "local pbf failed: empty graph"}
        return
    # pyrosm returns tuple (nodes_gdf, edges_gdf) when nodes=True. Convert to graph.
    try:
        if hasattr(net, "nodes") and hasattr(net, "edges") and not isinstance(net, (tuple, list)):
            Gp = net
        else:
            # Expect tuple (nodes_gdf, edges_gdf) from pyrosm when nodes=True.
            if not isinstance(net, (tuple, list)) or len(net) != 2:
                raise ValueError("missing nodes/edges from pyrosm")
            nodes_gdf, edges_gdf = net
            # Ensure x/y columns for osmnx
            if "x" not in nodes_gdf.columns and "lon" in nodes_gdf.columns:
                nodes_gdf = nodes_gdf.rename(columns={"lon": "x"})
            if "y" not in nodes_gdf.columns and "lat" in nodes_gdf.columns:
                nodes_gdf = nodes_gdf.rename(columns={"lat": "y"})
            if nodes_gdf is None or edges_gdf is None:
                raise ValueError("missing nodes/edges from pyrosm")
            # Ensure node index is osmid (required by osmnx 2.x)
            if "id" in nodes_gdf.columns:
                nodes_gdf = nodes_gdf.set_index("id")
                nodes_gdf.index.name = "osmid"
            # Ensure edge index is MultiIndex (u, v, key) with unique keys
            if "u" in edges_gdf.columns and "v" in edges_gdf.columns:
                # Drop edges referencing nodes not in nodes_gdf (bbox boundary)
                node_ids = set(nodes_gdf.index)
                edges_gdf = edges_gdf[
                    edges_gdf["u"].isin(node_ids) & edges_gdf["v"].isin(node_ids)
                ].copy()
                # Assign incrementing keys for duplicate (u, v) pairs
                edges_gdf["key"] = edges_gdf.groupby(["u", "v"]).cumcount()
                edges_gdf = edges_gdf.set_index(["u", "v", "key"])
            Gp = ox.graph_from_gdfs(nodes_gdf, edges_gdf)
    except Exception as e:
        _set_progress("error", f"local pbf failed: {e}")
        RESULTS[request_id] = {"ok": False, "error": f"local pbf failed: {e}"}
        return

    if Gp is None or len(Gp.nodes) == 0:
        _set_progress("error", "local pbf failed: empty graph")
        RESULTS[request_id] = {"ok": False, "error": "local pbf failed: empty graph"}
        return

    # Already bbox-filtered at pyrosm level, use Gp directly
    G = Gp

    _set_progress("prepare", f"sketch points {len(sketch)}", 35)

    sketch_norm = [(float(p.get("x", 0)), float(p.get("y", 0))) for p in sketch]
    cx = sum(p[0] for p in sketch_norm) / len(sketch_norm)
    cy = sum(p[1] for p in sketch_norm) / len(sketch_norm)

    scales = rca.TRANSFORM_SCALES
    rotations = rca.TRANSFORM_ROTATIONS
    stride = max(0.01, float(rca.TRANSFORM_TRANSLATE_STRIDE))
    translations = []
    t = 0.0
    while t <= 1.00001:
        translations.append(round(t, 4))
        t += stride
    translations = [(x, y) for x in translations for y in translations]

    max_attempts = rca.TRANSFORM_MAX_ATTEMPTS
    max_candidates = max(top_k * rca.TRANSFORM_MAX_CANDIDATES_FACTOR, top_k)
    target_score = rca.TRANSFORM_TARGET_SCORE

    transforms = [(s, r, txy) for s in scales for r in rotations for txy in translations]
    transforms = transforms[:max_attempts]

    def apply_transform(points, scale, deg, tx, ty):
        rad = math.radians(deg)
        cosv = math.cos(rad)
        sinv = math.sin(rad)
        out = []
        for x, y in points:
            dx = (x - cx) * scale
            dy = (y - cy) * scale
            rx = dx * cosv - dy * sinv
            ry = dx * sinv + dy * cosv
            out.append((rx + tx, ry + ty))
        return out

    def to_latlng(norm_pts):
        pts = []
        for x, y in norm_pts:
            lat = south + (1 - y) * (north - south)
            lng = west + x * (east - west)
            pts.append((lat, lng))
        return pts

    def nodes_to_coords(path_nodes):
        return [[G.nodes[n]["y"], G.nodes[n]["x"]] for n in path_nodes]

    candidates = []
    for idx, (s, r, (tx, ty)) in enumerate(transforms, 1):
        if len(candidates) >= max_candidates:
            break
        norm_pts = apply_transform(sketch_norm, s, r, tx, ty)
        in_count = sum(1 for x, y in norm_pts if 0 <= x <= 1 and 0 <= y <= 1)
        if in_count < max(2, int(len(norm_pts) * 0.7)):
            continue
        pts_latlng = to_latlng(norm_pts)
        try:
            nodes = [ox.distance.nearest_nodes(G, X=p[1], Y=p[0]) for p in pts_latlng]
        except Exception:
            continue
        nodes = [n for i, n in enumerate(nodes) if i == 0 or n != nodes[i - 1]]
        if len(nodes) < 2:
            continue
        try:
            full_path = []
            for i in range(len(nodes) - 1):
                seg = nx.shortest_path(G, nodes[i], nodes[i + 1], weight="length")
                if full_path:
                    full_path.extend(seg[1:])
                else:
                    full_path.extend(seg)
        except Exception:
            continue
        coords = nodes_to_coords(full_path)
        score = rca.polyline_distance_score([(c[0], c[1]) for c in coords], pts_latlng)
        length_m = rca.route_length_m(G, full_path)
        candidates.append({
            "coords": coords,
            "score": round(score, 4),
            "length_m": round(length_m, 1)
        })
        if score <= target_score:
            pass
        if idx % 20 == 0:
            _set_progress("search", f"transform {idx}/{len(transforms)} · found {len(candidates)}", 70 + int(20 * idx / max(1, len(transforms))))

    candidates.sort(key=lambda x: (x["score"], x["length_m"]))
    top = candidates[:top_k]
    _set_progress("done", f"ok {len(top)}", 100)
    RESULTS[request_id] = {
        "ok": True,
        "candidates": top,
        "best": top[0] if top else None,
        "debug": {"candidate_count": len(candidates)}
    }


def main():
    # 固定区域 (北京小矩形)
    area = {
        "type": "bbox",
        "north": 39.9800,
        "south": 39.9000,
        "east": 116.4600,
        "west": 116.3200
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
    t = threading.Thread(target=_run_match_local_only, args=(request_id, area, sketch, top_k), daemon=True)
    t.start()

    print("\n=== Progress (live) ===")
    last_key = None
    while t.is_alive():
        info = PROGRESS.get(request_id)
        if info:
            key = f"{info.get('status')}|{info.get('detail')}|{info.get('percent')}"
            if key != last_key:
                last_key = key
                percent = info.get("percent")
                suffix = f" · {percent}%" if isinstance(percent, int) else ""
                print(f"{info.get('status')} · {info.get('detail')}{suffix}")
        time.sleep(0.6)
    t.join()

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
