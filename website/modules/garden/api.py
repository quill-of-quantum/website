import copy
import json
import os
import time

from flask import Blueprint, jsonify, render_template, request


bp = Blueprint("garden", __name__)

BASE_DIR = "/home/bbdwz/projects/website/data/garden"
CURRENT_PATH = os.path.join(BASE_DIR, "current.json")
GARDENS_PATH = os.path.join(BASE_DIR, "gardens.json")
EVENTS_PATH = os.path.join(BASE_DIR, "events.jsonl")
PLANTS_PATH = os.path.join(BASE_DIR, "plants.json")


def _default_state(left_rows=8, right_rows=8, name="默认菜地", garden_id="default"):
    now = time.time()
    return {
        "id": garden_id,
        "name": name,
        "version": 1,
        "layout": {
            "left_rows": left_rows,
            "right_rows": right_rows,
            "beds": {}
        },
        "created_at": now,
        "updated_at": now
    }


def _read_json(path, fallback):
    if not os.path.exists(path):
        return copy.deepcopy(fallback)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return copy.deepcopy(fallback)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _load_state():
    state = _read_json(CURRENT_PATH, _default_state())
    state.setdefault("version", 1)
    state.setdefault("layout", {})
    state["layout"].setdefault("left_rows", 8)
    state["layout"].setdefault("right_rows", 8)
    state["layout"].setdefault("beds", {})
    return state


def _save_state(state):
    state["updated_at"] = time.time()
    _write_json(CURRENT_PATH, state)


def _load_gardens():
    data = _read_json(GARDENS_PATH, None)
    if isinstance(data, dict) and isinstance(data.get("gardens"), list):
        data["gardens"] = [_normalize_state(garden) for garden in data["gardens"]]
        return data

    old_state = _normalize_state(_load_state())
    old_state["id"] = old_state.get("id") or "default"
    old_state["name"] = old_state.get("name") or "默认菜地"
    return {"gardens": [old_state]}


def _save_gardens(data):
    _write_json(GARDENS_PATH, data)


def _normalize_state(state):
    if not isinstance(state, dict):
        state = _default_state()
    state.setdefault("id", "default")
    state.setdefault("name", "默认菜地")
    state.setdefault("version", 1)
    state.setdefault("created_at", time.time())
    state.setdefault("updated_at", time.time())
    state.setdefault("layout", {})
    state["layout"].setdefault("left_rows", 8)
    state["layout"].setdefault("right_rows", 8)
    state["layout"].setdefault("beds", {})
    for row_id in list(state["layout"]["beds"].keys()):
        _ensure_row(state["layout"], row_id)
    return state


def _find_garden(data, garden_id):
    for garden in data.get("gardens", []):
        if garden.get("id") == garden_id:
            return garden
    return None


def _garden_summary(garden):
    layout = garden.get("layout", {})
    return {
        "id": garden.get("id"),
        "name": garden.get("name", "未命名菜地"),
        "left_rows": layout.get("left_rows", 0),
        "right_rows": layout.get("right_rows", 0),
        "version": garden.get("version", 1),
        "updated_at": garden.get("updated_at")
    }


def _make_garden_id():
    return f"garden-{int(time.time() * 1000)}"


def _clean_row_count(value):
    return max(1, min(int(value), 30))


def _append_event(change, state):
    event = {
        "ts": time.time(),
        "garden_id": state.get("id"),
        "version": state.get("version"),
        "change": change
    }
    os.makedirs(BASE_DIR, exist_ok=True)
    with open(EVENTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _row_id(side, index):
    return f"{side}-{index}"


def _ensure_row(layout, row_id):
    beds = layout.setdefault("beds", {})
    if row_id not in beds:
        beds[row_id] = [{"name": ""}]
    if not beds[row_id]:
        beds[row_id] = [{"name": ""}]
    beds[row_id] = [_normalize_plot(plot) for plot in beds[row_id]]
    return beds[row_id]


def _normalize_plot(plot):
    if not isinstance(plot, dict):
        plot = {"name": str(plot)}
    plot.setdefault("name", "")
    plot.setdefault("history", [])
    if not isinstance(plot["history"], list):
        plot["history"] = []
    plot["history"] = [
        {
            "name": str(item.get("name", "")) if isinstance(item, dict) else str(item),
            "names": item.get("names", []) if isinstance(item, dict) and isinstance(item.get("names"), list) else [],
            "changed_at": item.get("changed_at") if isinstance(item, dict) else None,
            "change_type": item.get("change_type") if isinstance(item, dict) else None
        }
        for item in plot["history"]
    ]
    plot["recent_history"] = plot["history"][:2]
    layout_previous = plot.get("layout_previous")
    if isinstance(layout_previous, dict) and isinstance(layout_previous.get("sources"), list):
        normalized_sources = []
        for source in layout_previous["sources"]:
            if not isinstance(source, dict):
                continue
            source_plot = _normalize_plot(source.get("plot", source))
            normalized_sources.append({
                "plot_index": source.get("plot_index"),
                "name": source_plot.get("name", ""),
                "history": source_plot.get("history", []),
                "layout_previous": source_plot.get("layout_previous")
            })
        plot["layout_previous"] = {
            "changed_at": layout_previous.get("changed_at"),
            "from_count": layout_previous.get("from_count"),
            "to_count": layout_previous.get("to_count"),
            "sources": normalized_sources
        }
    return plot


def _empty_plot():
    return {"name": "", "history": []}


def _plot_source_names(plot):
    layout_previous = plot.get("layout_previous") if isinstance(plot, dict) else None
    if isinstance(layout_previous, dict):
        names = [
            str(source.get("name", "")).strip()
            for source in layout_previous.get("sources", [])
            if isinstance(source, dict) and str(source.get("name", "")).strip()
        ]
        if names:
            return names
    name = str(plot.get("name", "")).strip() if isinstance(plot, dict) else ""
    return [name] if name else []


def _plot_history_entry(plot, change_type):
    names = _plot_source_names(plot)
    return {
        "name": str(plot.get("name", "")),
        "names": names,
        "changed_at": time.time(),
        "change_type": change_type
    }


def _source_indexes(old_count, new_index, new_count):
    if old_count <= 0:
        return []
    start = int(new_index * old_count / new_count)
    end = int((new_index + 1) * old_count / new_count)
    if end <= start:
        end = start + 1
    return list(range(start, min(end, old_count)))


def _apply_change(state, change):
    change_type = change.get("type")
    payload = change.get("payload") or {}
    layout = state.setdefault("layout", {})

    if change_type == "split_row":
        row_id = str(payload.get("row_id") or "")
        count = max(1, min(int(payload.get("count", 1)), 6))
        if not row_id:
            raise ValueError("缺少 row_id")
        old_plots = _ensure_row(layout, row_id)
        old_count = len(old_plots)
        new_plots = []
        for index in range(count):
            source_indexes = _source_indexes(old_count, index, count)
            sources = [copy.deepcopy(_normalize_plot(old_plots[source_index])) for source_index in source_indexes]
            primary = old_plots[index] if index < old_count else (sources[0] if sources else _empty_plot())
            new_plot = copy.deepcopy(_normalize_plot(primary))
            if count != old_count:
                new_plot["layout_previous"] = {
                    "changed_at": time.time(),
                    "from_count": old_count,
                    "to_count": count,
                    "sources": [
                        {
                            "plot_index": source_index,
                            "plot": copy.deepcopy(_normalize_plot(old_plots[source_index]))
                        }
                        for source_index in source_indexes
                    ]
                }
            new_plots.append(_normalize_plot(new_plot))
        layout.setdefault("beds", {})[row_id] = new_plots
        return {"row_id": row_id, "count": count, "plots": new_plots}

    if change_type == "set_plot":
        row_id = str(payload.get("row_id") or "")
        plot_index = int(payload.get("plot_index", 0))
        name = str(payload.get("name") or "").strip()
        if not row_id:
            raise ValueError("缺少 row_id")
        if plot_index < 0:
            raise ValueError("plot_index 不能小于 0")
        plots = _ensure_row(layout, row_id)
        while len(plots) <= plot_index:
            plots.append(_empty_plot())
        plot = _normalize_plot(plots[plot_index])
        old_name = str(plot.get("name", ""))
        if name != old_name:
            plot["history"] = [_plot_history_entry(plot, "set_plot")] + plot.get("history", [])
            plot["name"] = name
        plots[plot_index] = plot
        return {"row_id": row_id, "plot_index": plot_index, "plot": plot}

    if change_type == "delete_history":
        row_id = str(payload.get("row_id") or "")
        plot_index = int(payload.get("plot_index", 0))
        delete_current = bool(payload.get("current"))
        history_index_value = payload.get("history_index")
        record_index = int(payload.get("record_index", -1))
        if not row_id:
            raise ValueError("缺少 row_id")
        if plot_index < 0:
            raise ValueError("plot_index 不能小于 0")
        plots = _ensure_row(layout, row_id)
        if plot_index >= len(plots):
            raise ValueError("地块不存在")
        plot = _normalize_plot(plots[plot_index])
        history = plot.get("history", [])

        if delete_current:
            deleted = {
                "name": plot.get("name", ""),
                "names": _plot_source_names(plot),
                "changed_at": plot.get("updated_at"),
                "change_type": "current"
            }
            if history:
                promoted = history.pop(0)
                plot["name"] = str(promoted.get("name", ""))
            else:
                plot["name"] = ""
        else:
            if history_index_value is not None:
                history_index = int(history_index_value)
            else:
                if record_index < 0:
                    raise ValueError("record_index 不能小于 0")
                record_count = len(history) + 1
                if record_index >= record_count:
                    raise ValueError("记录不存在")
                history_index = len(history) - 1 - record_index
            if history_index < 0 or history_index >= len(history):
                raise ValueError("历史记录不存在")
            deleted = history.pop(history_index)

        plot["history"] = history
        plot["recent_history"] = history[:2]
        plots[plot_index] = plot
        return {
            "row_id": row_id,
            "plot_index": plot_index,
            "record_index": record_index,
            "current": delete_current,
            "deleted": deleted,
            "plot": plot
        }

    raise ValueError(f"不支持的变更类型: {change_type}")


def _load_plants():
    return _read_json(PLANTS_PATH, [])


def _state_response(state):
    return {
        "state": state,
        "plants": _load_plants()
    }


@bp.route("/garden")
def garden_page():
    return render_template("garden.html")


@bp.route("/garden/<garden_id>")
def garden_editor_page(garden_id):
    return render_template("garden.html", garden_id=garden_id)


@bp.route("/api/garden/list")
def api_garden_list():
    data = _load_gardens()
    return jsonify({
        "success": True,
        "data": {
            "gardens": [_garden_summary(garden) for garden in data["gardens"]]
        }
    })


@bp.route("/api/garden/create", methods=["POST"])
def api_garden_create():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "新菜地").strip() or "新菜地"
    try:
        left_rows = _clean_row_count(payload.get("left_rows", 8))
        right_rows = _clean_row_count(payload.get("right_rows", 8))
    except Exception:
        return jsonify({
            "success": False,
            "error": "左右行数必须是数字",
            "code": "INVALID_GARDEN_ROWS"
        }), 400

    data = _load_gardens()
    garden = _default_state(left_rows, right_rows, name, _make_garden_id())
    data["gardens"].append(garden)
    _save_gardens(data)
    _append_event({"type": "create_garden", "payload": payload}, garden)
    return jsonify({
        "success": True,
        "message": "已创建菜地",
        "data": {"garden": _garden_summary(garden)}
    })


@bp.route("/api/garden/delete/<garden_id>", methods=["POST"])
def api_garden_delete(garden_id):
    payload = request.get_json(silent=True) or {}
    if str(payload.get("password") or "") != "1234":
        return jsonify({
            "success": False,
            "error": "删除密码错误",
            "code": "INVALID_GARDEN_DELETE_PASSWORD"
        }), 403

    data = _load_gardens()
    garden = _find_garden(data, garden_id)
    if not garden:
        return jsonify({
            "success": False,
            "error": "菜地不存在",
            "code": "GARDEN_NOT_FOUND"
        }), 404

    data["gardens"] = [item for item in data["gardens"] if item.get("id") != garden_id]
    _save_gardens(data)
    _append_event({"type": "delete_garden", "payload": {"garden_id": garden_id}}, garden)
    return jsonify({
        "success": True,
        "message": "已删除菜地",
        "data": {"garden_id": garden_id}
    })


@bp.route("/api/garden/state")
def api_garden_state():
    data = _load_gardens()
    state = data["gardens"][0] if data["gardens"] else _default_state()
    return jsonify({
        "success": True,
        "data": _state_response(state)
    })


@bp.route("/api/garden/state/<garden_id>")
def api_garden_state_by_id(garden_id):
    data = _load_gardens()
    state = _find_garden(data, garden_id)
    if not state:
        return jsonify({
            "success": False,
            "error": "菜地不存在",
            "code": "GARDEN_NOT_FOUND"
        }), 404
    return jsonify({
        "success": True,
        "data": _state_response(state)
    })


@bp.route("/api/garden/change/<garden_id>", methods=["POST"])
def api_garden_change(garden_id):
    change = request.get_json(silent=True) or {}
    data = _load_gardens()
    state = _find_garden(data, garden_id)
    if not state:
        return jsonify({
            "success": False,
            "error": "菜地不存在",
            "code": "GARDEN_NOT_FOUND"
        }), 404

    base_version = change.get("base_version")
    if base_version is not None and int(base_version) != int(state.get("version", 0)):
        return jsonify({
            "success": False,
            "error": "版本已过期，请刷新后重试",
            "code": "VERSION_CONFLICT",
            "data": {"server_version": state.get("version")}
        }), 409

    try:
        changed = _apply_change(state, change)
    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "code": "INVALID_GARDEN_CHANGE"
        }), 400

    state["version"] = int(state.get("version", 0)) + 1
    state["updated_at"] = time.time()
    _save_gardens(data)
    _append_event(change, state)
    return jsonify({
        "success": True,
        "message": "已保存变更",
        "data": {
            "version": state["version"],
            "changed": changed
        }
    })


@bp.route("/api/garden/plants/search")
def api_garden_plants_search():
    q = (request.args.get("q") or "").strip().lower()
    plants = _load_plants()
    if not q:
        matches = plants[:12]
    else:
        matches = []
        for plant in plants:
            fields = [plant.get("name", "")] + list(plant.get("aliases", []))
            if any(q in str(field).lower() for field in fields):
                matches.append(plant)
            if len(matches) >= 12:
                break
    return jsonify({
        "success": True,
        "data": {"items": matches}
    })
