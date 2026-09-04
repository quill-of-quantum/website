"""In-memory, process-local registry for short-lived RTC sessions.

Media never passes through this registry. It only binds authenticated principals
to opaque, one-time credentials and isolates signaling between sessions.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
import uuid


SESSION_KINDS = {"call", "device_view", "screen_share", "generic"}
PARTICIPANT_ROLES = {"publish", "subscribe", "duplex"}


class RegistryError(ValueError):
    """An expected session or credential validation failure."""


def _token_digest(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _public_participant(participant: dict) -> dict:
    return {
        "participant_id": participant["participant_id"],
        "display_name": participant["display_name"],
        "role": participant["role"],
        "connected": bool(participant.get("sid")),
    }


class RtcRegistry:
    def __init__(self, clock=None):
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._sessions: dict[str, dict] = {}
        self._invites: dict[str, dict] = {}
        self._join_tokens: dict[str, dict] = {}
        self._sid_index: dict[str, tuple[str, str]] = {}

    def reset(self):
        """Clear transient state. Intended for tests and controlled shutdown."""
        with self._lock:
            self._sessions.clear()
            self._invites.clear()
            self._join_tokens.clear()
            self._sid_index.clear()

    def _now(self) -> int:
        return int(self._clock())

    def _new_token(self) -> str:
        return secrets.token_urlsafe(32)

    def _cleanup_unlocked(self):
        now = self._now()
        expired_sessions = [
            session_id
            for session_id, session in self._sessions.items()
            if session["expires_at"] <= now or session.get("ended_at")
        ]
        for session_id in expired_sessions:
            self._remove_session_unlocked(session_id)
        self._invites = {
            digest: record
            for digest, record in self._invites.items()
            if record["expires_at"] > now and record["session_id"] in self._sessions
        }
        self._join_tokens = {
            digest: record
            for digest, record in self._join_tokens.items()
            if record["expires_at"] > now and record["session_id"] in self._sessions
        }

    def _remove_session_unlocked(self, session_id: str):
        session = self._sessions.pop(session_id, None)
        if not session:
            return
        for participant in session["participants"].values():
            sid = participant.get("sid")
            if sid:
                self._sid_index.pop(sid, None)
        self._invites = {
            digest: record
            for digest, record in self._invites.items()
            if record["session_id"] != session_id
        }
        self._join_tokens = {
            digest: record
            for digest, record in self._join_tokens.items()
            if record["session_id"] != session_id
        }

    def _session_unlocked(self, session_id: str) -> dict:
        self._cleanup_unlocked()
        session = self._sessions.get(str(session_id or ""))
        if not session:
            raise RegistryError("session-not-found")
        return session

    @staticmethod
    def _participant_for_principal_unlocked(session: dict, principal: str):
        for participant in session["participants"].values():
            if participant["principal"] == principal:
                return participant
        return None

    def _issue_join_token_unlocked(self, session: dict, participant: dict, ttl: int = 60) -> str:
        token = self._new_token()
        self._join_tokens[_token_digest(token)] = {
            "session_id": session["session_id"],
            "participant_id": participant["participant_id"],
            "principal": participant["principal"],
            "expires_at": min(session["expires_at"], self._now() + max(10, min(int(ttl), 300))),
        }
        return token

    def create_session(
        self,
        principal: str,
        display_name: str,
        kind: str = "call",
        role: str = "duplex",
        max_participants: int = 2,
        ttl: int = 3600,
    ) -> tuple[dict, str]:
        kind = str(kind or "call")
        role = str(role or "duplex")
        if kind not in SESSION_KINDS:
            raise RegistryError("invalid-kind")
        if role not in PARTICIPANT_ROLES:
            raise RegistryError("invalid-role")
        max_participants = max(2, min(int(max_participants), 8))
        ttl = max(300, min(int(ttl), 24 * 3600))
        now = self._now()
        with self._lock:
            self._cleanup_unlocked()
            session_id = uuid.uuid4().hex
            participant_id = uuid.uuid4().hex
            participant = {
                "participant_id": participant_id,
                "principal": str(principal),
                "display_name": str(display_name)[:64],
                "role": role,
                "created_at": now,
                "sid": None,
            }
            session = {
                "session_id": session_id,
                "kind": kind,
                "created_at": now,
                "expires_at": now + ttl,
                "creator": str(principal),
                "max_participants": max_participants,
                "participants": {participant_id: participant},
            }
            self._sessions[session_id] = session
            token = self._issue_join_token_unlocked(session, participant)
            return self._public_session_unlocked(session, principal), token

    def create_invite(
        self,
        session_id: str,
        principal: str,
        role: str = "duplex",
        ttl: int = 600,
    ) -> tuple[str, int]:
        role = str(role or "duplex")
        if role not in PARTICIPANT_ROLES:
            raise RegistryError("invalid-role")
        with self._lock:
            session = self._session_unlocked(session_id)
            if not self._participant_for_principal_unlocked(session, principal):
                raise RegistryError("forbidden")
            if len(session["participants"]) >= session["max_participants"]:
                raise RegistryError("session-full")
            expires_at = min(session["expires_at"], self._now() + max(30, min(int(ttl), 3600)))
            token = self._new_token()
            self._invites[_token_digest(token)] = {
                "session_id": session["session_id"],
                "role": role,
                "expires_at": expires_at,
            }
            return token, expires_at

    def redeem_invite(self, token: str, principal: str, display_name: str) -> tuple[dict, str]:
        digest = _token_digest(token)
        with self._lock:
            self._cleanup_unlocked()
            invite = self._invites.get(digest)
            if not invite:
                raise RegistryError("invalid-invite")
            session = self._session_unlocked(invite["session_id"])
            if self._participant_for_principal_unlocked(session, principal):
                raise RegistryError("already-member")
            if len(session["participants"]) >= session["max_participants"]:
                raise RegistryError("session-full")
            self._invites.pop(digest, None)
            participant_id = uuid.uuid4().hex
            participant = {
                "participant_id": participant_id,
                "principal": str(principal),
                "display_name": str(display_name)[:64],
                "role": invite["role"],
                "created_at": self._now(),
                "sid": None,
            }
            session["participants"][participant_id] = participant
            join_token = self._issue_join_token_unlocked(session, participant)
            return self._public_session_unlocked(session, principal), join_token

    def issue_join_token(self, session_id: str, principal: str) -> str:
        with self._lock:
            session = self._session_unlocked(session_id)
            participant = self._participant_for_principal_unlocked(session, principal)
            if not participant:
                raise RegistryError("forbidden")
            return self._issue_join_token_unlocked(session, participant)

    def join_socket(self, token: str, principal: str, sid: str) -> tuple[dict, list[dict]]:
        digest = _token_digest(token)
        with self._lock:
            self._cleanup_unlocked()
            credential = self._join_tokens.get(digest)
            if not credential or credential["principal"] != principal:
                raise RegistryError("invalid-join-token")
            if str(sid) in self._sid_index:
                raise RegistryError("already-joined")
            session = self._session_unlocked(credential["session_id"])
            participant = session["participants"].get(credential["participant_id"])
            if not participant or participant["principal"] != principal:
                raise RegistryError("invalid-join-token")
            old_sid = participant.get("sid")
            if old_sid and old_sid != sid:
                raise RegistryError("already-connected")
            self._join_tokens.pop(digest, None)
            participant["sid"] = str(sid)
            participant["connected_at"] = self._now()
            self._sid_index[str(sid)] = (session["session_id"], participant["participant_id"])
            peers = [
                _public_participant(peer)
                for peer in session["participants"].values()
                if peer["participant_id"] != participant["participant_id"] and peer.get("sid")
            ]
            own = _public_participant(participant)
            own["session_id"] = session["session_id"]
            return own, peers

    def leave_socket(self, sid: str):
        with self._lock:
            location = self._sid_index.pop(str(sid), None)
            if not location:
                return None
            session = self._sessions.get(location[0])
            if not session:
                return None
            participant = session["participants"].get(location[1])
            if not participant or participant.get("sid") != sid:
                return None
            participant["sid"] = None
            return {
                "session_id": session["session_id"],
                "participant": _public_participant(participant),
            }

    def signal_target(self, sid: str, session_id: str, target_participant_id: str) -> tuple[dict, str]:
        with self._lock:
            self._cleanup_unlocked()
            location = self._sid_index.get(str(sid))
            if not location or location[0] != session_id:
                raise RegistryError("not-joined")
            session = self._session_unlocked(session_id)
            sender = session["participants"].get(location[1])
            target = session["participants"].get(str(target_participant_id or ""))
            if not sender or not target or not target.get("sid"):
                raise RegistryError("peer-not-connected")
            return _public_participant(sender), target["sid"]

    def session_for_principal(self, session_id: str, principal: str) -> dict:
        with self._lock:
            session = self._session_unlocked(session_id)
            if not self._participant_for_principal_unlocked(session, principal):
                raise RegistryError("forbidden")
            return self._public_session_unlocked(session, principal)

    def end_session(self, session_id: str, principal: str) -> list[str]:
        with self._lock:
            session = self._session_unlocked(session_id)
            if not self._participant_for_principal_unlocked(session, principal):
                raise RegistryError("forbidden")
            sids = [participant["sid"] for participant in session["participants"].values() if participant.get("sid")]
            self._remove_session_unlocked(session_id)
            return sids

    def _public_session_unlocked(self, session: dict, principal: str) -> dict:
        own = self._participant_for_principal_unlocked(session, principal)
        return {
            "session_id": session["session_id"],
            "kind": session["kind"],
            "created_at": session["created_at"],
            "expires_at": session["expires_at"],
            "max_participants": session["max_participants"],
            "participant_id": own["participant_id"] if own else None,
            "participants": [_public_participant(item) for item in session["participants"].values()],
        }


registry = RtcRegistry()
