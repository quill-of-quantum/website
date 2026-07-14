from flask import Blueprint, render_template, jsonify, request
import json
import os
import time
import requests
from datetime import datetime
import aacgmv2

bp = Blueprint("aurora", __name__, url_prefix="/aurora")

DATA_DIR = "/home/bbdwz/projects/website/data/aurora"
LOCATION_PATH = os.path.join(DATA_DIR, "selected_location.json")
DEFAULT_LOCATION = {
    "name": "Munich",
    "lat": 48.1374,
    "lon": 11.5755,
    "updated_at": None
}


@bp.route("/")
def aurora_page():
    return render_template("aurora.html")


@bp.route("/api/status")
def aurora_status():
    """
    Realtime Kp using NOAA SWPC planetary 1-minute index.
    """
    url = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
    try:
        resp = requests.get(url, timeout=6)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return jsonify({"ok": False, "error": "NOAA 数据为空", "source": "NOAA SWPC"}), 502
        latest = data[-1]
        payload = {
            "kp_index": latest.get("kp_index"),
            "estimated_kp": latest.get("estimated_kp"),
            "kp": latest.get("estimated_kp") if latest.get("estimated_kp") is not None else latest.get("kp_index"),
            "time_tag": latest.get("time_tag")
        }
        return jsonify({"ok": True, "source": "NOAA SWPC planetary_k_index_1m", "data": payload})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "source": "NOAA SWPC planetary_k_index_1m"}), 502


@bp.route("/api/forecast")
def aurora_forecast():
    """
    Short-term Kp trend using NOAA SWPC planetary 1-minute index.
    """
    url = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
    try:
        resp = requests.get(url, timeout=6)
        resp.raise_for_status()
        raw = resp.json()
        x = []
        y = []
        recent = raw[-180:] if len(raw) > 180 else raw
        for item in recent:
            ts = item.get("time_tag")
            k = item.get("estimated_kp")
            if k is None:
                k = item.get("kp_index")
            if ts is None or k is None:
                continue
            x.append(ts)
            y.append(k)
        return jsonify({
            "ok": True,
            "source": "NOAA SWPC planetary_k_index_1m",
            "data": {"x": x, "y": y}
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "source": "NOAA SWPC planetary_k_index_1m"}), 502


def _normalize_lon(lon):
    lon = lon % 360
    if lon < 0:
        lon += 360
    return lon


def _nearest_ovation_point(lat, lon, coordinates):
    norm_lon = _normalize_lon(lon)
    best = None
    best_dist = None
    for item in coordinates:
        try:
            pt_lon, pt_lat, aurora = item
        except ValueError:
            continue
        pt_lon = _normalize_lon(pt_lon)
        lon_diff = abs(norm_lon - pt_lon)
        lon_diff = min(lon_diff, 360 - lon_diff)
        dist = (lat - pt_lat) ** 2 + (lon_diff ** 2)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = {
                "lat": pt_lat,
                "lon": pt_lon,
                "aurora": aurora
            }
    return best


def _get_kp_now():
    url = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
    resp = requests.get(url, timeout=6)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError("NOAA KP 数据为空")
    latest = data[-1]
    kp_value = latest.get("estimated_kp")
    if kp_value is None:
        kp_value = latest.get("kp_index")
    if kp_value is None:
        raise ValueError("NOAA KP 数据缺少值")
    return float(kp_value)


def _estimate_visibility(kp, mag_lat):
    boundary = 65 - 5 * kp
    margin = mag_lat - boundary
    if margin <= -5:
        prob = 0
    elif margin < 0:
        prob = int((margin + 5) / 5 * 30)
    elif margin < 5:
        prob = int(30 + (margin / 5) * 50)
    else:
        prob = int(80 + min((margin - 5) * 4, 20))
    prob = max(0, min(prob, 100))
    return {
        "boundary_lat": boundary,
        "margin": margin,
        "estimated_probability": prob
    }


@bp.route("/api/location_estimate")
def aurora_location_estimate():
    """
    Estimate local visibility using KP + geomagnetic latitude (AACGMv2).
    """
    lat = request.args.get("lat")
    lon = request.args.get("lon")
    if lat is None or lon is None:
        location = _read_location()
        lat = location["lat"]
        lon = location["lon"]
    else:
        lat = float(lat)
        lon = float(lon)

    try:
        kp = _get_kp_now()
        mag_lat, mag_lon, _ = aacgmv2.get_aacgm_coord(lat, lon, 0, datetime.utcnow())
        estimate = _estimate_visibility(kp, mag_lat)
        return jsonify({
            "ok": True,
            "source": "KP + AACGMv2 (estimate)",
            "requested": {"lat": lat, "lon": lon},
            "kp": kp,
            "magnetic_lat": mag_lat,
            "magnetic_lon": mag_lon,
            **estimate
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "source": "KP + AACGMv2 (estimate)"}), 502


@bp.route("/api/ovation")
def aurora_ovation():
    """
    Location-based aurora probability using NOAA OVATION grid.
    """
    lat = request.args.get("lat")
    lon = request.args.get("lon")
    if lat is None or lon is None:
        location = _read_location()
        lat = location["lat"]
        lon = location["lon"]
    else:
        lat = float(lat)
        lon = float(lon)

    url = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
    try:
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        coordinates = data.get("coordinates", [])
        nearest = _nearest_ovation_point(lat, lon, coordinates)
        if not nearest:
            return jsonify({"ok": False, "error": "无法匹配最近坐标点"}), 502
        return jsonify({
            "ok": True,
            "source": "NOAA SWPC OVATION",
            "requested": {"lat": lat, "lon": lon},
            "nearest": nearest,
            "observation_time": data.get("Observation Time"),
            "forecast_time": data.get("Forecast Time")
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "source": "NOAA SWPC OVATION"}), 502


@bp.route("/api/social")
def aurora_social():
    query = request.args.get("q", "aurora")
    return jsonify({
        "status": "placeholder",
        "query": query,
        "message": "社交媒体检索尚未接入。"
    })


def _read_location():
    if not os.path.exists(LOCATION_PATH):
        return DEFAULT_LOCATION.copy()
    try:
        with open(LOCATION_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_LOCATION.copy()


def _write_location(payload):
    data = {
        "name": payload.get("name") or "自定义",
        "lat": float(payload["lat"]),
        "lon": float(payload["lon"]),
        "updated_at": int(time.time())
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOCATION_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


@bp.route("/api/location", methods=["GET", "POST"])
def aurora_location():
    if request.method == "GET":
        return jsonify({"ok": True, "data": _read_location()})

    payload = request.get_json(silent=True) or {}
    if "lat" not in payload or "lon" not in payload:
        return jsonify({"ok": False, "error": "缺少经纬度参数"}), 400
    try:
        data = _write_location(payload)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "data": data})
