#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import math
import threading
import random
from datetime import datetime
from flask import Blueprint, request, jsonify

bp = Blueprint("route_creator", __name__, url_prefix="/api/route_creator")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "routes.json")
PROGRESS = {}
RESULTS = {}


def load_routes():
    if not os.path.exists(DATA_FILE):
        return {"routes": []}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"routes": []}


def save_routes(payload):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


@bp.route("/list", methods=["GET"])
def list_routes():
    data = load_routes()
    return jsonify({"routes": data.get("routes", [])})


@bp.route("/save", methods=["POST"])
def save_route():
    payload = request.get_json(silent=True) or {}
    name = payload.get("name", "").strip() or "untitled"
    points = payload.get("points", [])
    notes = payload.get("notes", "")
    if not isinstance(points, list) or not points:
        return jsonify({"ok": False, "error": "points required"}), 400

    data = load_routes()
    routes = data.get("routes", [])
    route_id = payload.get("id") or f"rt_{int(datetime.utcnow().timestamp())}"
    now = datetime.utcnow().isoformat(timespec="seconds")

    new_entry = {
        "id": route_id,
        "name": name,
        "notes": notes,
        "points": points,
        "updated_at": now,
        "created_at": payload.get("created_at") or now
    }

    replaced = False
    for i, r in enumerate(routes):
        if r.get("id") == route_id:
            routes[i] = new_entry
            replaced = True
            break
    if not replaced:
        routes.append(new_entry)

    save_routes({"routes": routes})
    return jsonify({"ok": True, "id": route_id})


@bp.route("/get/<route_id>", methods=["GET"])
def get_route(route_id):
    data = load_routes()
    for r in data.get("routes", []):
        if r.get("id") == route_id:
            return jsonify(r)
    return jsonify({"ok": False, "error": "not found"}), 404


@bp.route("/progress", methods=["GET"])
def get_progress():
    request_id = request.args.get("request_id")
    if not request_id:
        return jsonify({"ok": False, "error": "request_id required"}), 400
    info = PROGRESS.get(request_id)
    if not info:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "progress": info})


@bp.route("/result", methods=["GET"])
def get_result():
    request_id = request.args.get("request_id")
    if not request_id:
        return jsonify({"ok": False, "error": "request_id required"}), 400
    info = RESULTS.get(request_id)
    if not info:
        return jsonify({"ok": False, "error": "not ready"}), 404
    return jsonify({"ok": True, "result": info})


def _set_progress(request_id, status, detail, percent=None):
    payload = {
        "status": status,
        "detail": detail,
        "ts": datetime.utcnow().isoformat()
    }
    if percent is not None:
        try:
            payload["percent"] = max(0, min(100, int(percent)))
        except Exception:
            pass
    PROGRESS[request_id] = payload


def _set_result(request_id, payload):
    RESULTS[request_id] = payload


def haversine_m(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, asin, sqrt
    r = 6371000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def point_to_segment_distance(p, a, b):
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    if dx == 0 and dy == 0:
        return ((p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2) ** 0.5
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx = a[0] + t * dx
    cy = a[1] + t * dy
    return ((p[0] - cx) ** 2 + (p[1] - cy) ** 2) ** 0.5


def normalize_points(points):
    xs = [p[1] for p in points]
    ys = [p[0] for p in points]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    dx = maxx - minx
    dy = maxy - miny
    scale = max(dx, dy) if max(dx, dy) > 0 else 1.0
    return [((p[1] - minx) / scale, (p[0] - miny) / scale) for p in points]


def polyline_distance_score(path_pts, sketch_pts, sample=180):
    if len(path_pts) < 2 or len(sketch_pts) < 2:
        return float("inf")
    path_norm = normalize_points(path_pts)
    sketch_norm = normalize_points(sketch_pts)
    step = max(1, int(len(path_norm) / sample))
    sampled = path_norm[::step]
    segs = list(zip(sketch_norm[:-1], sketch_norm[1:]))
    total = 0.0
    for p in sampled:
        dmin = min(point_to_segment_distance(p, a, b) for a, b in segs)
        total += dmin
    return total / max(1, len(sampled))


def polyline_distance_score_rot(path_pts, sketch_pts, sample=180, angles_deg=None):
    if len(path_pts) < 2 or len(sketch_pts) < 2:
        return float("inf")
    if angles_deg is None:
        angles_deg = list(range(0, 360, 10))
    path_norm = normalize_points(path_pts)
    sketch_norm = normalize_points(sketch_pts)
    cx = sum(p[0] for p in sketch_norm) / len(sketch_norm)
    cy = sum(p[1] for p in sketch_norm) / len(sketch_norm)

    step = max(1, int(len(path_norm) / sample))
    sampled = path_norm[::step]

    best = float("inf")
    for ang in angles_deg:
        rad = math.radians(ang)
        cosv = math.cos(rad)
        sinv = math.sin(rad)
        rotated = []
        for x, y in sketch_norm:
            dx = x - cx
            dy = y - cy
            rx = cx + dx * cosv - dy * sinv
            ry = cy + dx * sinv + dy * cosv
            rotated.append((rx, ry))
        segs = list(zip(rotated[:-1], rotated[1:]))
        total = 0.0
        for p in sampled:
            dmin = min(point_to_segment_distance(p, a, b) for a, b in segs)
            total += dmin
        score = total / max(1, len(sampled))
        if score < best:
            best = score
    return best


def route_length_m(G, path_nodes):
    total = 0.0
    for u, v in zip(path_nodes[:-1], path_nodes[1:]):
        data = G.get_edge_data(u, v) or {}
        if not data:
            continue
        total += min((d.get("length", 0.0) for d in data.values()), default=0.0)
    return total


def _to_local_xy(lat, lng, center_lat):
    # Equirectangular approximation.
    x = lng * 111320.0 * max(0.1, abs(math.cos(math.radians(center_lat))))
    y = lat * 110540.0
    return x, y


def _resample_polyline(points, segment_len_m):
    if len(points) < 2:
        return points
    resampled = [points[0]]
    remaining = segment_len_m
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        dx = x2 - x1
        dy = y2 - y1
        seg_len = (dx * dx + dy * dy) ** 0.5
        if seg_len == 0:
            continue
        while seg_len >= remaining:
            t = remaining / seg_len
            nx = x1 + dx * t
            ny = y1 + dy * t
            resampled.append((nx, ny))
            x1, y1 = nx, ny
            dx = x2 - x1
            dy = y2 - y1
            seg_len = (dx * dx + dy * dy) ** 0.5
            remaining = segment_len_m
        remaining -= seg_len
    if resampled[-1] != points[-1]:
        resampled.append(points[-1])
    return resampled


@bp.route("/match", methods=["POST"])
def match_route():
    try:
        payload = request.get_json(silent=True) or {}
        area = payload.get("area") or {}
        sketch = payload.get("sketch") or []
        top_k = int(payload.get("top_k") or 5)
        request_id = payload.get("request_id") or f"req_{int(datetime.utcnow().timestamp())}"
        _set_progress(request_id, "queued", "accepted", 3)
        RESULTS.pop(request_id, None)

        if not area or not sketch or len(sketch) < 2:
            _set_progress(request_id, "error", "area and sketch required")
            return jsonify({"ok": False, "error": "area and sketch required"}), 400

        def run_job():
            try:
                _set_progress(request_id, "start", "init", 5)
                _run_match(request_id, area, sketch, top_k)
            except Exception as e:
                _set_progress(request_id, "error", f"server error: {e}")
                _set_result(request_id, {"ok": False, "error": f"server error: {e}"})

        t = threading.Thread(target=run_job, daemon=True)
        t.start()
        return jsonify({"ok": True, "request_id": request_id})

    except Exception as e:
        return jsonify({"ok": False, "error": f"server error: {e}"}), 500


def _run_match(request_id, area, sketch, top_k):
    def _safe_float(v):
        try:
            return float(v)
        except Exception:
            return None
    try:
        import osmnx as ox
        import networkx as nx
    except Exception:
        _set_progress(request_id, "error", "missing osmnx/networkx")
        _set_result(request_id, {"ok": False, "error": "missing osmnx/networkx dependencies"})
        return

    try:
        ox.settings.use_cache = True
        ox.settings.log_console = False
        ox.settings.timeout = 60
        ox.settings.max_query_area_size = 5e6
    except Exception:
        pass

    def area_bbox(a):
        if a.get("type") == "bbox":
            n = _safe_float(a.get("north"))
            s = _safe_float(a.get("south"))
            e = _safe_float(a.get("east"))
            w = _safe_float(a.get("west"))
            if None in (n, s, e, w):
                return None
            return n, s, e, w
        if a.get("type") == "circle":
            lat = _safe_float(a.get("lat"))
            lng = _safe_float(a.get("lng"))
            radius_km = _safe_float(a.get("radius_km", 1.0))
            if None in (lat, lng, radius_km):
                return None
            radius_m = radius_km * 1000.0
            dlat = radius_m / 111320.0
            dlng = radius_m / (111320.0 * max(0.1, abs(math.cos(math.radians(lat)))))
            return lat + dlat, lat - dlat, lng + dlng, lng - dlng
        if a.get("type") == "polygon":
            pts = a.get("points", [])
            lats = [_safe_float(p.get("lat")) for p in pts]
            lngs = [_safe_float(p.get("lng")) for p in pts]
            if any(v is None for v in lats + lngs):
                return None
            if not lats or not lngs:
                return None
            return max(lats), min(lats), max(lngs), min(lngs)
        return None

    bbox = area_bbox(area)
    if not bbox:
        _set_progress(request_id, "error", "cannot build area bbox")
        _set_result(request_id, {"ok": False, "error": "cannot build area bbox"})
        return
    north, south, east, west = bbox
    if north < south:
        north, south = south, north
    if east < west:
        east, west = west, east
    if not (-90 <= north <= 90 and -90 <= south <= 90 and -180 <= east <= 180 and -180 <= west <= 180):
        _set_progress(request_id, "error", f"invalid bbox {north},{south},{east},{west}")
        _set_result(request_id, {"ok": False, "error": "invalid bbox range"})
        return
    center_lat = (north + south) / 2.0
    width_km = abs(east - west) * 111.32 * max(0.1, abs(math.cos(math.radians(center_lat))))
    height_km = abs(north - south) * 111.32
    area_km2 = width_km * height_km
    _set_progress(request_id, "debug", f"bbox {north:.6f},{south:.6f},{east:.6f},{west:.6f} area {area_km2:.2f} km2", 8)
    if area_km2 > 900.0:
        _set_progress(request_id, "error", "area too large")
        _set_result(request_id, {"ok": False, "error": f"area too large ({area_km2:.1f} km2). please shrink area"})
        return

    area_type = area.get("type")
    # Minimal road network: keep only highway geometry, include all highway types.
    custom_filter = '["highway"]'
    def graph_from_bbox_compat(n, s, e, w):
        # osmnx signature and bbox order vary across versions.
        version = getattr(ox, "__version__", "0")
        major = int(str(version).split(".")[0] or 0)
        if major >= 2:
            # osmnx 2.x expects bbox as (left, bottom, right, top) => (west, south, east, north)
            try:
                return ox.graph_from_bbox((w, s, e, n), custom_filter=custom_filter, simplify=True, retain_all=False)
            except TypeError:
                pass
        try:
            return ox.graph_from_bbox((n, s, e, w), custom_filter=custom_filter, simplify=True, retain_all=False)
        except TypeError:
            pass
        try:
            return ox.graph_from_bbox(n, s, e, w, custom_filter=custom_filter, simplify=True, retain_all=False)
        except TypeError:
            pass
        return ox.graph_from_bbox(
            north=n, south=s, east=e, west=w,
            custom_filter=custom_filter, simplify=True, retain_all=False
        )
    def build_graph():
        if area_type == "bbox":
            return graph_from_bbox_compat(
                area["north"], area["south"], area["east"], area["west"]
            )
        if area_type == "circle":
            center = (area["lat"], area["lng"])
            dist = float(area.get("radius_km", 1.0)) * 1000.0
            return ox.graph_from_point(
                center,
                dist=dist,
                custom_filter=custom_filter,
                simplify=True,
                retain_all=False
            )
        if area_type == "polygon":
            from shapely.geometry import Polygon
            coords = [(p["lng"], p["lat"]) for p in area.get("points", [])]
            if len(coords) < 3:
                return None, "polygon requires 3+ points"
            return ox.graph_from_polygon(
                Polygon(coords),
                custom_filter=custom_filter,
                simplify=True,
                retain_all=False
            )
        return None, "unknown area type"

    overpass_endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.nchc.org.tw/api/interpreter"
    ]

    G = None
    last_error = None
    for i, endpoint in enumerate(overpass_endpoints, 1):
        try:
            try:
                ox.settings.overpass_endpoint = endpoint
            except Exception:
                pass
            _set_progress(request_id, "fetch", f"overpass {endpoint}", 10 + i * 5)
            result = build_graph()
            if isinstance(result, tuple):
                _set_progress(request_id, "error", result[1])
                _set_result(request_id, {"ok": False, "error": result[1]})
                return
            G = result
            if G is not None and len(G.nodes) > 0:
                break
        except Exception as e:
            last_error = f"{endpoint} -> {e}"
            G = None
            continue

    if G is None:
        _set_progress(request_id, "error", f"graph build failed: {last_error or 'unknown'}")
        _set_result(request_id, {"ok": False, "error": f"graph build failed: {last_error or 'unknown'}"})
        return

    if G is None or len(G.nodes) == 0:
        _set_progress(request_id, "error", "empty road network")
        _set_result(request_id, {"ok": False, "error": "empty road network"})
        return

    sketch_pts = []
    if sketch and "x" in sketch[0]:
        for p in sketch:
            x = float(p.get("x", 0))
            y = float(p.get("y", 0))
            lat = south + (1 - y) * (north - south)
            lng = west + x * (east - west)
            sketch_pts.append((lat, lng))
    else:
        sketch_pts = [(p["lat"], p["lng"]) for p in sketch]

    _set_progress(request_id, "prepare", f"sketch points {len(sketch_pts)}", 35)

    def nodes_to_coords(path_nodes):
        return [[G.nodes[n]["y"], G.nodes[n]["x"]] for n in path_nodes]

    # Shape-guided beam search over the road graph.
    nodes_list = list(G.nodes)
    if len(nodes_list) < 2:
        _set_progress(request_id, "error", "empty road network")
        _set_result(request_id, {"ok": False, "error": "empty road network"})
        return

    rand = random.Random()
    try:
        rand.seed(request_id)
    except Exception:
        pass

    center_lat = (north + south) / 2.0
    sketch_xy = [_to_local_xy(lat, lng, center_lat) for lat, lng in sketch_pts]
    total_len = 0.0
    for a, b in zip(sketch_xy[:-1], sketch_xy[1:]):
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        total_len += (dx * dx + dy * dy) ** 0.5
    seg_count = max(20, min(80, int(total_len / 150.0) if total_len > 0 else 20))
    seg_len = max(40.0, total_len / max(1, seg_count))
    resampled = _resample_polyline(sketch_xy, seg_len)
    if len(resampled) < 2:
        _set_progress(request_id, "error", "sketch too short")
        _set_result(request_id, {"ok": False, "error": "sketch too short"})
        return

    dir_angles = []
    for a, b in zip(resampled[:-1], resampled[1:]):
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        if dx == 0 and dy == 0:
            dir_angles.append(0.0)
        else:
            dir_angles.append(math.atan2(dy, dx))

    beam_width = max(20, min(80, top_k * 5))
    start_count = max(8, min(30, top_k * 4))
    max_candidates = max(top_k * 6, top_k)
    target_score = 0.06

    start_nodes = [rand.choice(nodes_list) for _ in range(start_count)]
    beam = []
    for n in start_nodes:
        beam.append({
            "path": [n],
            "cost": 0.0,
            "len": 0.0,
            "prev_dir": None
        })

    candidates = []
    _set_progress(request_id, "search", f"beam search K={beam_width}", 70)

    def angle_diff(a, b):
        d = abs(a - b) % (2 * math.pi)
        return d if d <= math.pi else (2 * math.pi - d)

    w_angle = 1.0
    w_len = 0.25
    w_turn = 0.15

    for i, target_theta in enumerate(dir_angles, 1):
        next_states = []
        for state in beam:
            u = state["path"][-1]
            for v in G.successors(u):
                data = G.get_edge_data(u, v) or {}
                if not data:
                    continue
                length = min((d.get("length", 0.0) for d in data.values()), default=0.0)
                if length <= 0:
                    continue
                try:
                    uy, ux = G.nodes[u]["y"], G.nodes[u]["x"]
                    vy, vx = G.nodes[v]["y"], G.nodes[v]["x"]
                    x1, y1 = _to_local_xy(uy, ux, center_lat)
                    x2, y2 = _to_local_xy(vy, vx, center_lat)
                    theta = math.atan2(y2 - y1, x2 - x1)
                except Exception:
                    continue

                adiff = angle_diff(theta, target_theta)
                len_pen = abs(length - seg_len) / max(1.0, seg_len)
                turn_pen = 0.0
                if state["prev_dir"] is not None:
                    turn_pen = angle_diff(theta, state["prev_dir"]) / math.pi

                cost = state["cost"] + w_angle * adiff + w_len * len_pen + w_turn * turn_pen
                next_states.append({
                    "path": state["path"] + [v],
                    "cost": cost,
                    "len": state["len"] + length,
                    "prev_dir": theta
                })

        if not next_states:
            break

        next_states.sort(key=lambda s: s["cost"])
        beam = next_states[:beam_width]

        if i % 5 == 0 or i == len(dir_angles):
            pct = 70 + int(25 * i / max(1, len(dir_angles)))
            _set_progress(request_id, "search", f"beam step {i}/{len(dir_angles)}", pct)

    for state in beam:
        path = state["path"]
        if len(path) < 2:
            continue
        coords = nodes_to_coords(path)
        score = polyline_distance_score_rot([(c[0], c[1]) for c in coords], sketch_pts)
        candidates.append({
            "coords": coords,
            "score": round(score, 4),
            "length_m": round(state["len"], 1)
        })
        if len(candidates) >= max_candidates:
            break

    candidates.sort(key=lambda x: (x["score"], x["length_m"]))
    top = candidates[:top_k]
    _set_progress(request_id, "done", f"ok {len(top)}", 100)
    _set_result(request_id, {
        "ok": True,
        "candidates": top,
        "best": top[0] if top else None,
        "debug": {
            "area_km2": round(area_km2, 2),
            "nodes": len(G.nodes),
            "edges": len(G.edges),
            "sketch_points": len(sketch_pts),
            "candidate_count": len(candidates),
            "beam_width": beam_width,
            "segments": len(dir_angles),
            "request_id": request_id
        }
    })
