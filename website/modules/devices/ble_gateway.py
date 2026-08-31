"""BlueZ/Bleak central gateway for approved ESP32-S3 peripherals."""

import argparse
import asyncio
import json
import logging
import os
import secrets
import signal
import time
from datetime import datetime, timezone

from bleak import BleakClient, BleakScanner

from modules.devices import photo_service, storage
from modules.devices.ble_protocol import (
    COMMAND_UUID, EVENT_UUID, FLAG_BINARY, FLAG_JSON, FrameAssembler,
    ProtocolError, SERVICE_UUID, decode_json, encode_payload,
)
from modules.devices.bluez_agent import BluezAgent

LOG = logging.getLogger("device_ble_gateway")
STATUS_PATH = os.path.join(storage.BASE_DIR, "ble_gateway.json")


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class StatusWriter:
    def __init__(self, path=STATUS_PATH):
        self.path = path
        self.state = {"running": False, "scanning": False, "started_at": None, "active_connections": 0,
                      "sessions_ok": 0, "sessions_failed": 0, "last_device_id": None,
                      "last_session_at": None, "last_error": None}

    def update(self, **values):
        self.state.update(values); self.state["updated_at"] = int(time.time())
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)


class GatewaySession:
    def __init__(self, client, address, status_writer):
        self.client = client; self.address = address; self.status_writer = status_writer
        self.assembler = FrameAssembler(timeout_sec=30); self.queue = asyncio.Queue()
        self.message_id = secrets.randbits(31) or 1; self.device_id = None; self.nonce = secrets.token_hex(16)
        self.photo_headers = {}; self.last_activity = time.monotonic(); self.record = None

    def _notification(self, _characteristic, data):
        try:
            complete = self.assembler.feed(data)
            if complete: self.queue.put_nowait(complete)
        except Exception as exc:
            self.queue.put_nowait(exc)

    async def send(self, payload, flags=FLAG_JSON, message_id=None):
        message_id = int(message_id if message_id is not None else self.message_id)
        self.message_id = (self.message_id + 1) & 0x7FFFFFFF or 1
        mtu = max(23, min(int(getattr(self.client, "mtu_size", 247) or 247), 517))
        for frame in encode_payload(payload, message_id, mtu=mtu, flags=flags):
            await self.client.write_gatt_char(COMMAND_UUID, frame, response=True)

    async def result(self, request, ok=True, **values):
        payload = {"type": "result", "request_id": request.get("request_id"), "ok": bool(ok), **values}
        await self.send(payload)

    async def authenticate(self):
        await self.send({"type":"challenge", "protocol":1, "nonce":self.nonce,
                         "server_time":int(time.time()), "photo_max_bytes":524288})
        complete = await asyncio.wait_for(self.queue.get(), timeout=10)
        if isinstance(complete, Exception): raise complete
        message_id, flags, payload = complete
        if flags != FLAG_JSON:
            raise ProtocolError("auth-message-required")
        message = decode_json(payload)
        if message.get("type") != "auth" or message.get("nonce") != self.nonce:
            raise ProtocolError("invalid-auth-message")
        self.device_id = str(message.get("device_id") or "")
        LOG.info("BLE auth request from %s", self.device_id)
        record, error = storage.verify_ble_proof(self.device_id, self.nonce, message.get("proof"))
        if error:
            await self.result(message, False, error=error); raise ProtocolError(error)
        self.record=record
        LOG.info("BLE authenticated device=%s config_version=%s", self.device_id, record["config_version"])
        await self.send({"type":"auth_ok", "ok":True, "session_id":secrets.token_hex(8),
                         "server_time":int(time.time()), "config":record["config"],
                         "config_version":record["config_version"]})
        storage.note_ble_session(self.device_id, self.address, "authenticated")

    async def handle_json(self, message_id, message):
        kind = str(message.get("type") or "")
        LOG.info("BLE message device=%s type=%s request_id=%s", self.device_id, kind, message.get("request_id"))
        body = message.get("body") if isinstance(message.get("body"), dict) else {}
        if kind == "heartbeat":
            record, error = storage.contact_trusted(self.device_id, "ble", "heartbeat", body)
            if error: return await self.result(message, False, error=error)
            self.record=record
            return await self.result(message, True, status="approved", server_time=int(time.time()),
                                     config=record["config"], config_version=record["config_version"])
        if kind == "status":
            record, error = storage.contact_trusted(self.device_id, "ble", "status", body)
            if error: return await self.result(message, False, error=error)
            self.record=record
            return await self.result(message, True, received=True, config_version=record["config_version"],
                                     config_synced=record["config_synced"])
        if kind == "telemetry":
            record, error = storage.record_telemetry_trusted(self.device_id, body, "ble")
            if error: return await self.result(message, False, error=error)
            self.record=record
            return await self.result(message, True, received=True, config=record["config"],
                                     config_version=record["config_version"])
        if kind == "photo_begin":
            transfer_id = message.get("transfer_id")
            photo_id = str(message.get("photo_id") or "").lower()
            if not isinstance(transfer_id, int) or not (0 < transfer_id <= 0xFFFFFFFF):
                return await self.result(message, False, error="invalid-transfer-id")
            if not __import__("re").fullmatch(r"[0-9a-f]{32}", photo_id):
                return await self.result(message, False, error="invalid-photo-id")
            size = message.get("size_bytes")
            config=(self.record or {}).get("config") or {}; allowed=bool(config.get("ble_photo_enabled",True)); limit=min(524288,int(config.get("ble_photo_max_bytes",524288) or 524288))
            if not allowed:
                return await self.result(message, False, error="ble-photo-disabled", wifi_required=True)
            if isinstance(size, bool) or not isinstance(size, int) or size < 1 or size > limit:
                return await self.result(message, False, error="ble-photo-too-large", wifi_required=True)
            captured, error = photo_service.normalize_captured_at(message.get("captured_at"))
            if error: return await self.result(message, False, error=error)
            self.photo_headers[transfer_id] = {"photo_id":photo_id, "size":size,
                                               "captured_at":captured[0], "captured_source":captured[1],
                                               "request":message}
            return await self.result(message, True, ready=True, transfer_id=transfer_id)
        if kind == "done":
            await self.result(message, True, complete=True); return "done"
        return await self.result(message, False, error="unsupported-message-type")

    async def handle_binary(self, message_id, payload):
        LOG.info("BLE binary message device=%s message_id=%s bytes=%s", self.device_id, message_id, len(payload))
        header = self.photo_headers.pop(message_id, None)
        if header is None: raise ProtocolError("photo-begin-required")
        if len(payload) != header["size"]: raise ProtocolError("photo-size-mismatch")
        info, error = photo_service.inspect_jpeg(payload)
        if error:
            await self.result(header["request"], False, error=error); return
        metadata, duplicate, error = storage.record_photo_trusted(
            self.device_id, header["photo_id"], payload, info, header["captured_at"],
            header["captured_source"], "ble")
        if error: return await self.result(header["request"], False, error=error)
        await self.result(header["request"], True, photo_id=header["photo_id"],
                          duplicate=duplicate, photo=metadata)

    async def run(self):
        # BlueZ otherwise reports the minimum MTU 23 even after negotiation.
        acquire=getattr(getattr(self.client,"_backend",None),"_acquire_mtu",None)
        if acquire:
            try: await acquire()
            except Exception as exc: LOG.info("MTU acquisition fallback to 23: %s",exc)
        await self.client.start_notify(EVENT_UUID, self._notification)
        await self.authenticate()
        # A JPEG transfer can legitimately take considerably longer than the
        # command/heartbeat exchange.  The previous 12s watchdog could fire
        # immediately after photo_begin while the peripheral was still
        # sending the binary payload, causing a needless Wi-Fi fallback.
        while self.client.is_connected:
            complete = await asyncio.wait_for(self.queue.get(), timeout=60)
            if isinstance(complete, Exception): raise complete
            message_id, flags, payload = complete; self.last_activity = time.monotonic()
            if flags == FLAG_JSON:
                if await self.handle_json(message_id, decode_json(payload)) == "done": return
            elif flags == FLAG_BINARY:
                await self.handle_binary(message_id, payload)


class BleGateway:
    def __init__(self, scan_timeout=0):
        self.scan_timeout = scan_timeout; self.status = StatusWriter(); self.active = set(); self.pending = set()
        self.tasks = set(); self.semaphore = asyncio.Semaphore(4); self.stopping = asyncio.Event(); self.bluez_agent=BluezAgent()

    def detected(self, device, advertisement):
        uuids = {str(value).lower() for value in (advertisement.service_uuids or [])}
        if SERVICE_UUID not in uuids or device.address in self.active or device.address in self.pending: return
        # Advertisement callbacks can be delivered several times before the
        # scheduled coroutine gets a chance to enter connect().  Reserve the
        # address synchronously so one peripheral cannot create concurrent
        # BleakClient sessions.
        self.pending.add(device.address)
        task = asyncio.create_task(self.connect(device)); self.tasks.add(task); task.add_done_callback(self.tasks.discard)

    async def connect(self, device):
        async with self.semaphore:
            self.pending.discard(device.address)
            if device.address in self.active: return
            self.active.add(device.address); self.status.update(active_connections=len(self.active))
            self.bluez_agent.agent.allow(device.address)
            session = None
            try:
                async with BleakClient(device, timeout=12, pair=True, services=[SERVICE_UUID]) as client:
                    session = GatewaySession(client, device.address, self.status); await session.run()
                self.status.update(sessions_ok=self.status.state["sessions_ok"]+1,
                                   last_device_id=session.device_id if session else None,
                                   last_session_at=int(time.time()), last_error=None)
            except Exception as exc:
                LOG.warning("BLE session %s failed: %s", device.address, exc)
                if session and session.device_id: storage.note_ble_session(session.device_id, device.address, "failed", str(exc))
                self.status.update(sessions_failed=self.status.state["sessions_failed"]+1,
                                   last_device_id=session.device_id if session else None,
                                   last_session_at=int(time.time()), last_error=str(exc)[:300])
            finally:
                self.bluez_agent.agent.deny(device.address)
                self.active.discard(device.address); self.status.update(active_connections=len(self.active))
                self.pending.discard(device.address)

    async def run(self):
        self.status.update(running=True, scanning=True, started_at=int(time.time()), last_error=None)
        try:
            await self.bluez_agent.start(); LOG.info("BlueZ NoInputNoOutput pairing agent registered")
            scanner = BleakScanner(self.detected, service_uuids=[SERVICE_UUID])
            async with scanner:
                if self.scan_timeout:
                    await asyncio.wait_for(self.stopping.wait(), timeout=self.scan_timeout)
                else: await self.stopping.wait()
        except asyncio.TimeoutError:
            pass
        finally:
            await self.bluez_agent.stop()
            self.status.update(running=False, scanning=False)
            if self.tasks: await asyncio.gather(*self.tasks, return_exceptions=True)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--scan-timeout",type=int,default=0); parser.add_argument("--debug",action="store_true")
    args=parser.parse_args(); logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    gateway=BleGateway(args.scan_timeout); loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT,signal.SIGTERM): loop.add_signal_handler(sig,gateway.stopping.set)
    try: loop.run_until_complete(gateway.run())
    finally: loop.close()


if __name__ == "__main__": main()
