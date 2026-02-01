#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
from datetime import datetime
from flask import Blueprint, request, jsonify

bp = Blueprint("route_creator", __name__, url_prefix="/api/route_creator")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "routes.json")


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
