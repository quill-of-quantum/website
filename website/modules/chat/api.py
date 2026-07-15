import json
import os
import random
import re
import threading
import time
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, request
from flask_socketio import emit, join_room

from modules.game.api import socketio


bp = Blueprint("chat", __name__)

CHAT_ROOM = "chat_lobby"
CHAT_DATA_DIR = "/home/bbdwz/projects/website/data/chat"
CHAT_STATE_PATH = os.path.join(CHAT_DATA_DIR, "lobby.json")
MAX_MESSAGES = 300
STATE_LOCK = threading.Lock()

ADJECTIVES = [
    "晴天", "夜行", "像素", "闪电", "温柔", "安静", "飞驰", "蓝莓",
    "薄荷", "月光", "热心", "机智", "海风", "橙子", "银色", "小小",
    "清晨", "黄昏", "雨后", "微光", "跳跃", "快乐", "认真", "慢热",
    "好奇", "勇敢", "轻快", "透明", "暖暖", "冷静", "自由", "幸运",
    "白昼", "午夜", "遥远", "附近", "流动", "圆滚", "柔软", "锋利",
    "明亮", "低调", "神秘", "浪漫", "干净", "松弛", "敏捷", "可靠",
    "柠檬", "蜜桃", "奶油", "焦糖", "森林", "湖边", "山顶", "港口",
    "霓虹", "复古", "迷你", "巨型", "准时", "发光", "会飞", "发呆",
]
NOUNS = [
    "旅人", "车手", "园丁", "船长", "画家", "骑士", "云朵", "灯塔",
    "松饼", "火箭", "路标", "星星", "键盘", "邮差", "水手", "橡果",
    "面包", "贝壳", "气球", "风筝", "雨伞", "书签", "相机", "电台",
    "列车", "港湾", "岛屿", "山谷", "溪流", "森林", "花园", "广场",
    "杯子", "闹钟", "铅笔", "地图", "指南针", "望远镜", "收音机", "机器人",
    "咖啡", "奶茶", "曲奇", "布丁", "煎饼", "披萨", "寿司", "面条",
    "猎手", "法师", "侦探", "导演", "诗人", "鼓手", "飞行员", "工程师",
    "小队长", "观察员", "记录员", "冒险家", "收藏家", "修理工", "守门员", "播报员",
]
AVATARS = [
    "🍎", "🍏", "🍐", "🍊", "🍋", "🍌", "🍉", "🍇", "🍓", "🫐",
    "🍈", "🍒", "🍑", "🥭", "🍍", "🥥", "🥝", "🍅", "🥑", "🥕",
    "🌽", "🥦", "🥒", "🌶️", "🫑", "🥔", "🍠", "🥐", "🥯", "🍞",
    "🥨", "🧀", "🥞", "🧇", "🍔", "🍟", "🍕", "🌭", "🥪", "🌮",
    "🌯", "🥙", "🧆", "🍜", "🍝", "🍣", "🍤", "🥟", "🍩", "🍪",
    "🎂", "🧁", "🥧", "🍫", "🍬", "🍭", "🍮", "🍯", "☕", "🧋",
    "🚗", "🚕", "🚙", "🚌", "🚎", "🏎️", "🚓", "🚑", "🚒", "🚚",
    "🚛", "🚜", "🛵", "🏍️", "🚲", "🛴", "🚂", "🚆", "🚇", "🚊",
    "✈️", "🚁", "🚀", "🛸", "⛵", "🚤", "🛥️", "🚢", "⚓", "🛟",
    "🌵", "🌲", "🌳", "🌴", "🌱", "🌿", "🍀", "🍁", "🍄", "🌻",
    "🌼", "🌷", "🌹", "🪷", "🌙", "☀️", "⭐", "🌟", "⚡", "🔥",
    "💧", "❄️", "🌈", "☁️", "⛄", "🌊", "🪨", "🎈", "🎁", "🎲",
    "🎯", "🎮", "🕹️", "🎧", "🎤", "🎸", "🥁", "🎹", "🎺", "🎨",
    "📚", "📖", "✏️", "🖊️", "📷", "🎥", "📺", "📻", "💡", "🔦",
    "🧭", "🧰", "🔧", "🔭", "🧪", "🧲", "🧵", "🪡", "🧸", "🪁",
    "⌚", "📱", "💻", "🖥️", "⌨️", "🖱️", "💾", "💿", "📦", "📮",
]


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_device_id(value):
    text = str(value or "").strip()
    if re.match(r"^[a-zA-Z0-9._:-]{8,120}$", text):
        return text
    return uuid.uuid4().hex


def _default_state():
    return {"version": 1, "profiles": {}, "messages": []}


def _load_state_unlocked():
    if not os.path.exists(CHAT_STATE_PATH):
        return _default_state()
    try:
        with open(CHAT_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _default_state()
    except Exception:
        return _default_state()
    data.setdefault("version", 1)
    if not isinstance(data.get("profiles"), dict):
        data["profiles"] = {}
    if not isinstance(data.get("messages"), list):
        data["messages"] = []
    return data


def _save_state_unlocked(data):
    os.makedirs(CHAT_DATA_DIR, exist_ok=True)
    data["messages"] = sorted(data.get("messages", []), key=lambda msg: msg.get("ts", ""))[-MAX_MESSAGES:]
    tmp_path = CHAT_STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, CHAT_STATE_PATH)


def _random_profile(existing_names=None):
    existing_names = existing_names or set()
    for _ in range(30):
        name = f"{random.choice(ADJECTIVES)}{random.choice(NOUNS)}"
        if name not in existing_names:
            break
    else:
        name = f"{random.choice(ADJECTIVES)}{random.choice(NOUNS)}{random.randint(10, 99)}"
    return {
        "name": name,
        "avatar": random.choice(AVATARS),
        "updated_at": _now_iso(),
    }


def _ensure_profile_unlocked(state, device_id):
    profiles = state.setdefault("profiles", {})
    profile = profiles.get(device_id)
    if profile and profile.get("name") and profile.get("avatar"):
        return profile
    existing_names = {p.get("name") for p in profiles.values() if isinstance(p, dict)}
    profile = _random_profile(existing_names)
    profile["created_at"] = _now_iso()
    profiles[device_id] = profile
    return profile


def _public_message(message, profiles):
    profile = profiles.get(message.get("device_id"), {}) or {}
    return {
        "id": message.get("id"),
        "device_id": message.get("device_id"),
        "text": message.get("text", ""),
        "ts": message.get("ts", ""),
        "sender": {
            "name": profile.get("name", "匿名旅人"),
            "avatar": profile.get("avatar", "💬"),
        },
    }


def _public_state_unlocked(state, device_id=None):
    profiles = state.get("profiles", {})
    messages = sorted(state.get("messages", []), key=lambda msg: msg.get("ts", ""))
    payload = {
        "messages": [_public_message(message, profiles) for message in messages],
        "server_time": _now_iso(),
    }
    if device_id:
        payload["device_id"] = device_id
        payload["me"] = profiles.get(device_id)
    return payload


def _messages_since_unlocked(state, since):
    profiles = state.get("profiles", {})
    messages = sorted(
        (
            message for message in state.get("messages", [])
            if not since or message.get("ts", "") > since
        ),
        key=lambda msg: msg.get("ts", ""),
    )
    return [_public_message(message, profiles) for message in messages]


def _public_profile(device_id, profile):
    return {
        "device_id": device_id,
        "sender": {
            "name": profile.get("name", "匿名旅人"),
            "avatar": profile.get("avatar", "💬"),
        },
        "server_time": _now_iso(),
    }


def _broadcast_message(message):
    with STATE_LOCK:
        state = _load_state_unlocked()
        payload = {
            "message": _public_message(message, state.get("profiles", {})),
            "server_time": _now_iso(),
        }
    socketio.emit("chat_new_message", payload, to=CHAT_ROOM)


def _broadcast_profile(device_id, profile):
    socketio.emit("chat_profile_changed", _public_profile(device_id, profile), to=CHAT_ROOM)


@bp.route("/chat")
def chat_page():
    return render_template("chat.html")


@bp.route("/api/chat/state")
def chat_state_api():
    device_id = _normalize_device_id(request.args.get("device_id"))
    with STATE_LOCK:
        state = _load_state_unlocked()
        _ensure_profile_unlocked(state, device_id)
        _save_state_unlocked(state)
        payload = _public_state_unlocked(state, device_id=device_id)
    return jsonify(payload)


@bp.route("/api/chat/sync")
def chat_sync_api():
    since = str(request.args.get("since") or "").strip()
    device_id = _normalize_device_id(request.args.get("device_id"))
    with STATE_LOCK:
        state = _load_state_unlocked()
        _ensure_profile_unlocked(state, device_id)
        _save_state_unlocked(state)
        payload = {
            "messages": _messages_since_unlocked(state, since),
            "server_time": _now_iso(),
            "device_id": device_id,
            "me": state.get("profiles", {}).get(device_id),
        }
    return jsonify(payload)


@socketio.on("chat_join")
def chat_join(data):
    data = data or {}
    device_id = _normalize_device_id(data.get("device_id"))
    with STATE_LOCK:
        state = _load_state_unlocked()
        _ensure_profile_unlocked(state, device_id)
        _save_state_unlocked(state)
        payload = _public_state_unlocked(state, device_id=device_id)
    join_room(CHAT_ROOM)
    emit("chat_joined", payload)


@socketio.on("chat_send")
def chat_send(data):
    data = data or {}
    device_id = _normalize_device_id(data.get("device_id"))
    text = str(data.get("text") or "").strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        emit("chat_error", {"error": "empty_message"})
        return
    if len(text) > 1000:
        text = text[:1000]

    with STATE_LOCK:
        state = _load_state_unlocked()
        _ensure_profile_unlocked(state, device_id)
        message = {
            "id": uuid.uuid4().hex,
            "device_id": device_id,
            "text": text,
            "ts": _now_iso(),
        }
        state.setdefault("messages", []).append(message)
        _save_state_unlocked(state)
    _broadcast_message(message)


@socketio.on("chat_shuffle_profile")
def chat_shuffle_profile(data):
    data = data or {}
    device_id = _normalize_device_id(data.get("device_id"))
    with STATE_LOCK:
        state = _load_state_unlocked()
        profiles = state.setdefault("profiles", {})
        existing_names = {
            profile.get("name")
            for key, profile in profiles.items()
            if key != device_id and isinstance(profile, dict)
        }
        old_created_at = (profiles.get(device_id) or {}).get("created_at") or _now_iso()
        profile = _random_profile(existing_names)
        profile["created_at"] = old_created_at
        profiles[device_id] = profile
        _save_state_unlocked(state)
        payload = _public_profile(device_id, profile)
    emit("chat_profile", payload)
    _broadcast_profile(device_id, profile)
