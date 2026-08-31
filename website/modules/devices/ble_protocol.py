"""Framing shared by the Raspberry Pi BLE gateway and ESP32-S3 protocol v1."""

import json
import struct
import time
import zlib

PROTOCOL_VERSION = 1
SERVICE_UUID = "7f510001-1b15-4e3d-8d53-6f6f64777a01"
COMMAND_UUID = "7f510002-1b15-4e3d-8d53-6f6f64777a01"  # Pi -> ESP32 write
EVENT_UUID = "7f510003-1b15-4e3d-8d53-6f6f64777a01"    # ESP32 -> Pi notify

MAGIC = b"BD"
FLAG_JSON = 0x01
FLAG_BINARY = 0x02
HEADER = struct.Struct("<2sBBIHHI")
MAX_JSON_BYTES = 64 * 1024
MAX_BINARY_BYTES = 512 * 1024
MAX_CHUNKS = 65535


class ProtocolError(ValueError):
    pass


def encode_payload(payload, message_id, mtu=247, flags=FLAG_JSON):
    if isinstance(payload, dict):
        payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        flags = FLAG_JSON
    payload = bytes(payload)
    limit = MAX_JSON_BYTES if flags == FLAG_JSON else MAX_BINARY_BYTES
    if len(payload) > limit:
        raise ProtocolError("message-too-large")
    # ATT notification/write payload is MTU - 3 bytes.
    chunk_size = max(1, int(mtu) - 3 - HEADER.size)
    chunk_count = max(1, (len(payload) + chunk_size - 1) // chunk_size)
    if chunk_count > MAX_CHUNKS:
        raise ProtocolError("too-many-chunks")
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    frames = []
    for index in range(chunk_count):
        chunk = payload[index * chunk_size:(index + 1) * chunk_size]
        frames.append(HEADER.pack(MAGIC, PROTOCOL_VERSION, flags, int(message_id), index, chunk_count, checksum) + chunk)
    return frames


def decode_json(payload):
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid-json") from exc
    if not isinstance(value, dict):
        raise ProtocolError("json-object-required")
    return value


class FrameAssembler:
    def __init__(self, timeout_sec=30):
        self.timeout_sec = max(1, int(timeout_sec))
        self.pending = {}

    def _expire(self):
        cutoff = time.monotonic() - self.timeout_sec
        for message_id in [key for key, value in self.pending.items() if value["updated"] < cutoff]:
            self.pending.pop(message_id, None)

    def feed(self, frame):
        self._expire()
        frame = bytes(frame)
        if len(frame) < HEADER.size:
            raise ProtocolError("short-frame")
        magic, version, flags, message_id, index, count, checksum = HEADER.unpack(frame[:HEADER.size])
        if magic != MAGIC or version != PROTOCOL_VERSION:
            raise ProtocolError("unsupported-protocol")
        if flags not in {FLAG_JSON, FLAG_BINARY} or count < 1 or index >= count:
            raise ProtocolError("invalid-frame")
        state = self.pending.get(message_id)
        signature = (flags, count, checksum)
        if state is None:
            state = {"signature": signature, "chunks": {}, "size": 0, "updated": time.monotonic()}
            self.pending[message_id] = state
        elif state["signature"] != signature:
            self.pending.pop(message_id, None)
            raise ProtocolError("frame-conflict")
        chunk = frame[HEADER.size:]
        old = state["chunks"].get(index)
        if old is None:
            state["chunks"][index] = chunk
            state["size"] += len(chunk)
        elif old != chunk:
            self.pending.pop(message_id, None)
            raise ProtocolError("chunk-conflict")
        limit = MAX_JSON_BYTES if flags == FLAG_JSON else MAX_BINARY_BYTES
        if state["size"] > limit:
            self.pending.pop(message_id, None)
            raise ProtocolError("message-too-large")
        state["updated"] = time.monotonic()
        if len(state["chunks"]) != count:
            return None
        payload = b"".join(state["chunks"][part] for part in range(count))
        self.pending.pop(message_id, None)
        if zlib.crc32(payload) & 0xFFFFFFFF != checksum:
            raise ProtocolError("crc-mismatch")
        return message_id, flags, payload
