"""Socket.IO signaling transport for WebRTC negotiation."""

from collections import defaultdict, deque
import threading
import time

from flask import request, session
from flask_socketio import emit, join_room, leave_room

from modules.auth.user_store import get_user_id, user_exists
from modules.realtime import socketio
from modules.rtc.registry import RegistryError, registry


RTC_NAMESPACE = "/rtc"
MAX_SDP_BYTES = 196_608
MAX_CANDIDATE_BYTES = 8_192


class _RateLimiter:
    def __init__(self, limit=180, window=60):
        self.limit = limit
        self.window = window
        self.lock = threading.Lock()
        self.events = defaultdict(deque)

    def allow(self, key):
        now = time.monotonic()
        with self.lock:
            events = self.events[key]
            while events and events[0] <= now - self.window:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True

    def discard(self, key):
        with self.lock:
            self.events.pop(key, None)


_signal_rate = _RateLimiter()


def _principal():
    username = str(session.get("user") or "") if session.get("logged_in") else ""
    if not username or not user_exists(username):
        return None
    return f"user:{get_user_id(username)}"


def _normalize_signal(kind, payload):
    if kind == "description":
        if not isinstance(payload, dict) or payload.get("type") not in {"offer", "answer", "rollback"}:
            return None, False
        sdp = payload.get("sdp", "")
        if not isinstance(sdp, str) or len(sdp.encode("utf-8")) > MAX_SDP_BYTES:
            return None, False
        if payload["type"] in {"offer", "answer"} and not sdp.startswith("v=0"):
            return None, False
        return {"type": payload["type"], "sdp": sdp}, True
    if kind == "candidate":
        if payload is None:
            return None, True
        if not isinstance(payload, dict):
            return None, False
        candidate = payload.get("candidate")
        if candidate is not None and (
            not isinstance(candidate, str) or len(candidate.encode("utf-8")) > MAX_CANDIDATE_BYTES
        ):
            return None, False
        sdp_mid = payload.get("sdpMid")
        sdp_mline = payload.get("sdpMLineIndex")
        username_fragment = payload.get("usernameFragment")
        if sdp_mid is not None and (not isinstance(sdp_mid, str) or len(sdp_mid) > 256):
            return None, False
        if sdp_mline is not None and (
            isinstance(sdp_mline, bool) or not isinstance(sdp_mline, int) or not 0 <= sdp_mline <= 128
        ):
            return None, False
        if username_fragment is not None and (
            not isinstance(username_fragment, str) or len(username_fragment) > 256
        ):
            return None, False
        return {
            "candidate": candidate,
            "sdpMid": sdp_mid,
            "sdpMLineIndex": sdp_mline,
            "usernameFragment": username_fragment,
        }, True
    if kind == "renegotiate":
        return ({}, True) if payload is None or payload == {} else (None, False)
    return None, False


@socketio.on("connect", namespace=RTC_NAMESPACE)
def rtc_connect():
    if not _principal():
        return False


@socketio.on("rtc_join", namespace=RTC_NAMESPACE)
def rtc_join(data):
    principal = _principal()
    if not principal:
        return {"ok": False, "error": "login-required"}
    token = str((data or {}).get("join_token") or "")
    try:
        participant, peers = registry.join_socket(token, principal, request.sid)
    except RegistryError as exc:
        return {"ok": False, "error": str(exc)}
    room = f"rtc:{participant['session_id']}"
    join_room(room)
    emit("rtc_peer_joined", participant, to=room, include_self=False)
    return {"ok": True, "participant": participant, "peers": peers}


@socketio.on("rtc_signal", namespace=RTC_NAMESPACE)
def rtc_signal(data):
    if not _signal_rate.allow(request.sid):
        return {"ok": False, "error": "rate-limited"}
    data = data or {}
    session_id = str(data.get("session_id") or "")
    target_id = str(data.get("to") or "")
    kind = str(data.get("kind") or "")
    payload, valid = _normalize_signal(kind, data.get("payload"))
    if not valid:
        return {"ok": False, "error": "invalid-signal"}
    try:
        sender, target_sid = registry.signal_target(request.sid, session_id, target_id)
    except RegistryError as exc:
        return {"ok": False, "error": str(exc)}
    socketio.emit(
        "rtc_signal",
        {
            "session_id": session_id,
            "from": sender["participant_id"],
            "kind": kind,
            "payload": payload,
        },
        to=target_sid,
        namespace=RTC_NAMESPACE,
    )
    return {"ok": True}


def _leave_current_socket():
    result = registry.leave_socket(request.sid)
    if not result:
        return None
    room = f"rtc:{result['session_id']}"
    leave_room(room)
    emit(
        "rtc_peer_left",
        {
            "session_id": result["session_id"],
            "participant_id": result["participant"]["participant_id"],
        },
        to=room,
        include_self=False,
    )
    return result


@socketio.on("rtc_leave", namespace=RTC_NAMESPACE)
def rtc_leave():
    _leave_current_socket()
    return {"ok": True}


@socketio.on("disconnect", namespace=RTC_NAMESPACE)
def rtc_disconnect():
    _leave_current_socket()
    _signal_rate.discard(request.sid)
