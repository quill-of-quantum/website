import hashlib
import hmac
import os
import tempfile
import threading
import unittest

from modules.devices import data_store, storage
from modules.devices.ble_protocol import (
    FLAG_BINARY, FLAG_JSON, FrameAssembler, ProtocolError, decode_json,
    encode_payload,
)


class BleProtocolTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_paths = (storage.BASE_DIR, storage.STORE_PATH, storage.PHOTO_ROOT, storage.DATA_DB_PATH, storage.LEGACY_PHOTO_DB_PATH)
        storage.BASE_DIR = self.temporary.name
        storage.STORE_PATH = os.path.join(self.temporary.name, "devices.json")
        storage.PHOTO_ROOT = os.path.join(self.temporary.name, "photos")
        storage.DATA_DB_PATH = os.path.join(self.temporary.name, "device_data.sqlite3")
        storage.LEGACY_PHOTO_DB_PATH = os.path.join(self.temporary.name, "photos.sqlite3")
        data_store._MIGRATED.clear()

    def tearDown(self):
        storage.BASE_DIR, storage.STORE_PATH, storage.PHOTO_ROOT, storage.DATA_DB_PATH, storage.LEGACY_PHOTO_DB_PATH = self.original_paths
        data_store._MIGRATED.clear(); self.temporary.cleanup()

    def test_json_frames_reassemble_out_of_order(self):
        message = {"type":"telemetry","body":{"sample_id":"x"*32,"data":{"temperature_c":23.4}},"padding":"中"*200}
        frames = encode_payload(message, 1234, mtu=64)
        self.assertGreater(len(frames), 2)
        assembler = FrameAssembler()
        result = None
        for frame in reversed(frames):
            result = assembler.feed(frame) or result
        message_id, flags, payload = result
        self.assertEqual(message_id, 1234); self.assertEqual(flags, FLAG_JSON)
        self.assertEqual(decode_json(payload), message)

    def test_binary_frames_detect_corruption(self):
        frames = encode_payload(b"jpeg" * 100, 88, mtu=80, flags=FLAG_BINARY)
        damaged = list(frames); damaged[-1] = damaged[-1][:-1] + bytes([damaged[-1][-1] ^ 0x01])
        assembler = FrameAssembler()
        with self.assertRaisesRegex(ProtocolError, "crc-mismatch"):
            for frame in damaged: assembler.feed(frame)

    def test_ble_hmac_and_trusted_storage(self):
        storage.start_pairing(); secret="0123456789abcdef0123456789abcdef"; device_id="esp32s3-ble01"
        record, error = storage.register({"device_id":device_id,"device_secret":secret,"name":"BLE","device_type":"temperature"}, "127.0.0.1")
        self.assertIsNone(error); storage.approve(device_id)
        nonce="a"*32; key=hashlib.sha256(secret.encode()).digest()
        proof=hmac.new(key,b"bbdwz-ble-v1:"+nonce.encode(),hashlib.sha256).hexdigest()
        record,error=storage.verify_ble_proof(device_id,nonce,proof)
        self.assertIsNone(error); self.assertEqual(record["device_id"],device_id)
        _,error=storage.verify_ble_proof(device_id,nonce,"0"*64)
        self.assertEqual(error,"unauthorized")

        version=record["config_version"]
        record,error=storage.contact_trusted(device_id,"ble","status",{
            "config_version":version,
            "ble":{"bonded":True,"rssi":-50},
            "components":{"temperature_humidity":{"enabled":False,"code":"disabled"}},
        })
        self.assertIsNone(error); self.assertTrue(record["config_synced"])
        self.assertEqual(record["communication"],"ble"); self.assertEqual(record["ble_status"]["rssi"],-50)

        record,error=storage.record_telemetry_trusted(device_id,{"sample_id":"b"*32,"data":{"temperature_c":22.5}},"ble")
        self.assertIsNone(error); self.assertEqual(record["communication"],"ble")
        packet=storage.get_data_packet(record["id"])
        self.assertEqual(packet["categories"][0]["id"],"temperature")

    def test_temperature_profile_has_ble_fallback_defaults(self):
        config=storage.effective_config("temperature",{})
        self.assertEqual(config["ble_mode"],"off")
        self.assertEqual(config["ble_attempt_timeout_sec"],3)
        self.assertTrue(config["ble_photo_enabled"])
        self.assertEqual(config["ble_photo_max_bytes"],512*1024)

    def test_storage_lock_is_reentrant(self):
        with storage.LOCK:
            with storage.LOCK:
                storage._save_unlocked(storage._empty())
        self.assertTrue(os.path.exists(storage.STORE_PATH))


if __name__ == "__main__": unittest.main()
