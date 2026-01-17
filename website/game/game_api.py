from datetime import datetime, timedelta
import threading
import time

from flask import Blueprint, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room

bp = Blueprint("game", __name__, url_prefix="/game")
socketio = SocketIO(cors_allowed_origins="*", async_mode="gevent")

ROOM_LOCK = threading.Lock()
rooms = {}
sid_to_room = {}
_cleaner_started = False
connected_clients = 0


def _now():
    return datetime.utcnow()


def _default_state():
    return {
        "game": "placeholder",
        "game_type": None,
        "payload": {"message": "Game state placeholder"},
        "actions": [],
        "updated_at": time.time(),
    }


def _get_or_create_room(room_id, password):
    room = rooms.get(room_id)
    if room is None:
        room = {
            "room_id": room_id,
            "password": password or None,
            "players": set(),
            "seats": {},
            "game_state": _default_state(),
            "empty_since": None,
            "created_at": _now(),
        }
        rooms[room_id] = room
    return room


def _validate_room_password(room, password):
    if room["password"] and password != room["password"]:
        return False
    return True


def _assign_seat(room, sid):
    for seat in (1, 2):
        if seat not in room["seats"].values():
            room["seats"][sid] = seat
            return seat
    return None


def _remove_player(sid, force=False):
    room_id = sid_to_room.pop(sid, None)
    if not room_id:
        return None
    room = rooms.get(room_id)
    if not room:
        return None

    room["players"].discard(sid)
    room["seats"].pop(sid, None)
    leave_room(room_id)

    if not room["players"]:
        if force:
            rooms.pop(room_id, None)
            return {"room_id": room_id, "destroyed": True}
        room["empty_since"] = _now()
    else:
        room["empty_since"] = None
    return {"room_id": room_id, "destroyed": False}


def _room_summary(room):
    return {
        "room_id": room["room_id"],
        "players": len(room["players"]),
        "has_password": bool(room["password"]),
        "game_type": room["game_state"].get("game_type"),
        "created_at": room["created_at"].isoformat(),
    }


def _broadcast_lobby():
    summaries = [_room_summary(room) for room in rooms.values()]
    socketio.emit("lobby_rooms", {"rooms": summaries, "count": len(summaries)})


def _broadcast_stats():
    socketio.emit("lobby_stats", {"connected": connected_clients})


def start_room_cleaner():
    global _cleaner_started
    if _cleaner_started:
        return
    _cleaner_started = True

    def _cleaner():
        while True:
            time.sleep(5)
            now = _now()
            expired = []
            with ROOM_LOCK:
                for room_id, room in list(rooms.items()):
                    if room["empty_since"] and now - room["empty_since"] > timedelta(minutes=10):
                        expired.append(room_id)
                for room_id in expired:
                    rooms.pop(room_id, None)
                if expired:
                    _broadcast_lobby()

    threading.Thread(target=_cleaner, daemon=True).start()


@bp.route("/")
def game_index():
    room_id = request.args.get("room", "")
    return render_template("game.html", room_id=room_id)


@bp.route("/<room_id>")
def game_room(room_id):
    return render_template("game.html", room_id=room_id)


@socketio.on("join_room")
def handle_join(data):
    data = data or {}
    room_id = (data.get("room_id") or "").strip()
    password = data.get("password")
    game_type = data.get("game_type")

    if not room_id:
        emit("join_error", {"reason": "missing_room_id"})
        return

    with ROOM_LOCK:
        room = _get_or_create_room(room_id, password)
        if not _validate_room_password(room, password):
            emit("join_error", {"reason": "invalid_password"})
            return
        if len(room["players"]) >= 2 and request.sid not in room["players"]:
            emit("join_error", {"reason": "room_full"})
            return

        join_room(room_id)
        room["players"].add(request.sid)
        sid_to_room[request.sid] = room_id
        room["empty_since"] = None

        if room["game_state"]["game_type"] is None and game_type:
            room["game_state"]["game_type"] = game_type

        seat = room["seats"].get(request.sid) or _assign_seat(room, request.sid)
        emit(
            "join_ok",
            {
                "room_id": room_id,
                "seat": seat,
                "players": len(room["players"]),
                "game_state": room["game_state"],
            },
        )
        emit(
            "room_update",
            {"room_id": room_id, "players": len(room["players"]), "seats": room["seats"]},
            to=room_id,
        )
        _broadcast_lobby()


@socketio.on("leave_room")
def handle_leave(data):
    data = data or {}
    force = bool(data.get("force"))
    with ROOM_LOCK:
        result = _remove_player(request.sid, force=force)
        if not result:
            return
        if not result["destroyed"]:
            emit(
                "room_update",
                {
                    "room_id": result["room_id"],
                    "players": len(rooms[result["room_id"]]["players"]),
                    "seats": rooms[result["room_id"]]["seats"],
                },
                to=result["room_id"],
            )
        _broadcast_lobby()


@socketio.on("player_action")
def handle_action(data):
    data = data or {}
    action = data.get("action")
    with ROOM_LOCK:
        room_id = sid_to_room.get(request.sid)
        if not room_id or room_id not in rooms:
            emit("action_error", {"reason": "not_in_room"})
            return
        room = rooms[room_id]
        if action is None:
            emit("action_error", {"reason": "missing_action"})
            return

        actions = room["game_state"].setdefault("actions", [])
        actions.append({"sid": request.sid, "action": action, "ts": time.time()})
        if len(actions) > 50:
            del actions[:-50]
        room["game_state"]["updated_at"] = time.time()

        emit("state_update", room["game_state"], to=room_id)


@socketio.on("request_state")
def handle_request_state():
    with ROOM_LOCK:
        room_id = sid_to_room.get(request.sid)
        if not room_id or room_id not in rooms:
            emit("state_error", {"reason": "not_in_room"})
            return
        emit("state_update", rooms[room_id]["game_state"])


@socketio.on("request_lobby")
def handle_request_lobby():
    with ROOM_LOCK:
        _broadcast_lobby()
        _broadcast_stats()


@socketio.on("set_room_password")
def handle_set_room_password(data):
    data = data or {}
    new_password = data.get("password") or None
    with ROOM_LOCK:
        room_id = sid_to_room.get(request.sid)
        if not room_id or room_id not in rooms:
            emit("room_error", {"reason": "not_in_room"})
            return
        rooms[room_id]["password"] = new_password
        emit("room_password_updated", {"room_id": room_id, "has_password": bool(new_password)})
        _broadcast_lobby()


@socketio.on("connect")
def handle_connect():
    global connected_clients
    with ROOM_LOCK:
        connected_clients += 1
        _broadcast_stats()


@socketio.on("disconnect")
def handle_disconnect():
    with ROOM_LOCK:
        global connected_clients
        connected_clients = max(0, connected_clients - 1)
        result = _remove_player(request.sid, force=False)
        _broadcast_stats()
        if not result:
            return
        if not result["destroyed"]:
            emit(
                "room_update",
                {
                    "room_id": result["room_id"],
                    "players": len(rooms[result["room_id"]]["players"]),
                    "seats": rooms[result["room_id"]]["seats"],
                },
                to=result["room_id"],
            )
        _broadcast_lobby()
