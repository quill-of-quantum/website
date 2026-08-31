import io
import os
import sqlite3
import tempfile
import unittest

from flask import Flask
from PIL import Image

from modules.devices import data_store, storage
from modules.devices.api import bp


def _jpeg_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), (230, 120, 20)).save(buffer, "JPEG")
    return buffer.getvalue()


class DevicePhotoApiTest(unittest.TestCase):
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
        data_store._MIGRATED.clear()
        self.temporary.cleanup()

    def test_photo_upload_and_duplicate(self):
        storage.start_pairing()
        secret = "0123456789abcdef0123456789abcdef"
        device_id = "esp32s3-temp-camera01"
        record, error = storage.register({
            "device_id": device_id,
            "device_secret": secret,
            "name": "Camera",
            "device_type": "temperature",
            "capabilities": ["temperature", "humidity", "camera", "jpeg"],
        }, "127.0.0.1")
        self.assertIsNone(error)
        self.assertFalse(record["config"]["photo_enabled"])
        self.assertFalse(record["config"]["upload_enabled"])
        self.assertEqual(record["config_version"], 1)

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(bp)
        client = app.test_client()
        headers = {
            "Content-Type": "image/jpeg",
            "X-Device-ID": device_id,
            "X-Device-Secret": secret,
            "X-Device-Transport": "wifi/cpolar",
            "X-Photo-ID": "a" * 32,
            "X-Captured-At": "2026-08-30T12:30:00.123Z",
        }

        pending = client.post("/api/device/v1/photo", data=_jpeg_bytes(), headers=headers)
        self.assertEqual(pending.status_code, 403)
        self.assertEqual(pending.get_json()["error"], "device-pending")

        storage.approve(device_id)
        status_headers = {key:value for key,value in headers.items() if key not in {"Content-Type","X-Photo-ID","X-Captured-At"}}
        status = client.post("/api/device/v1/status", json={"config_version":1,"components":{"camera":{"enabled":True,"available":False,"code":"not-found"}}}, headers=status_headers)
        self.assertEqual(status.status_code, 200)
        saved_device = next(item for item in storage.list_devices() if item["device_id"] == device_id)
        self.assertFalse(saved_device["last_status"]["components"]["camera"]["available"])
        first = client.post("/api/device/v1/photo", data=_jpeg_bytes(), headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.get_json()["duplicate"])
        self.assertEqual(first.get_json()["photo"]["width"], 800)
        self.assertEqual(first.get_json()["photo"]["height"], 600)
        self.assertFalse(first.get_json()["config"]["photo_enabled"])

        duplicate = client.post("/api/device/v1/photo", data=_jpeg_bytes(), headers=headers)
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.get_json()["duplicate"])
        self.assertEqual(len(storage.list_photos(device_id)), 1)
        metadata, path = storage.get_photo(device_id, "a" * 32)
        self.assertEqual(metadata["captured_at"], "2026-08-30T12:30:00.123Z")
        self.assertTrue(path.endswith(".jpg"))

    def test_temperature_profile_migrates_once(self):
        data = storage._empty()
        data["devices"]["legacy"] = {
            "device_id": "legacy",
            "device_type": "temperature",
            "status": "approved",
            "config": {"sample_interval_sec": 30,"upload_enabled":True,"photo_enabled":True},
            "config_version": 3,
            "secret_hash": storage._hash_secret("0123456789abcdef"),
        }
        storage._save_unlocked(data)
        first = storage._load_unlocked()["devices"]["legacy"]
        second = storage._load_unlocked()["devices"]["legacy"]
        self.assertFalse(first["config"]["photo_enabled"])
        self.assertFalse(first["config"]["upload_enabled"])
        self.assertEqual(first["config"]["camera_resolution"], "800x600")
        self.assertEqual(first["config_version"], 4)
        self.assertEqual(second["config_version"], 4)

    def test_config_sync_requires_current_status_not_heartbeat(self):
        storage.start_pairing()
        secret = "0123456789abcdef0123456789abcdef"
        device_id = "esp32s3-sync01"
        storage.register({
            "device_id": device_id,
            "device_secret": secret,
            "name": "Sync test",
            "device_type": "temperature",
        }, "127.0.0.1")
        record = storage.approve(device_id)
        version = record["config_version"]

        # heartbeat 中的版本只是设备保存值，不能确认本周期已应用。
        record, error = storage.contact(
            device_id,
            secret,
            "127.0.0.1",
            kind="heartbeat",
            status_payload={
                "config_version": version,
            },
        )
        self.assertIsNone(error)
        self.assertEqual(record["device_config_version"], version)
        self.assertIsNone(record["config_ack_version"])
        self.assertFalse(record["config_synced"])

        # 当前版本的硬件状态表示设备已真正按这个版本运行。
        record, error = storage.contact(
            device_id,
            secret,
            "127.0.0.1",
            kind="status",
            status_payload={
                "config_version": version,
                "components": {
                    "camera": {"enabled": False, "code": "disabled"},
                },
            },
        )
        self.assertIsNone(error)
        self.assertTrue(record["config_synced"])
        self.assertEqual(record["config_ack_version"], version)
        self.assertEqual(record["config_ack_source"], "status")

        record, errors = storage.update_config(
            device_id, {"sample_interval_sec": 31}
        )
        self.assertEqual(errors, {})
        self.assertEqual(record["config_version"], version + 1)
        self.assertFalse(record["config_synced"])
        self.assertEqual(record["config_ack_version"], version)

        # 新版本必须先上报该版本的硬件状态，显式 ACK 也不能提前。
        rejected, error = storage.acknowledge_config(
            device_id, secret, version + 1, "127.0.0.1"
        )
        self.assertIsNone(rejected)
        self.assertEqual(error, "config-status-required")

        record, error = storage.contact(
            device_id,
            secret,
            "127.0.0.1",
            kind="status",
            status_payload={
                "config_version": version + 1,
                "components": {
                    "camera": {"enabled": False, "code": "disabled"},
                },
            },
        )
        self.assertIsNone(error)
        record, error = storage.acknowledge_config(
            device_id, secret, version + 1, "127.0.0.1"
        )
        self.assertIsNone(error)
        self.assertTrue(record["config_synced"])

    def test_server_version_recovers_when_device_is_ahead(self):
        data = storage._empty()
        data["devices"]["ahead"] = {
            "device_id": "ahead",
            "device_type": "temperature",
            "status": "approved",
            "config": storage.effective_config("temperature", {}),
            "config_version": 3,
            "device_config_version": 6,
            "profile_schema_revision": storage.schema_revision_for("temperature"),
            "secret_hash": storage._hash_secret("0123456789abcdef"),
        }
        storage._save_unlocked(data)

        first = storage._load_unlocked()["devices"]["ahead"]
        second = storage._load_unlocked()["devices"]["ahead"]
        self.assertEqual(first["config_version"], 7)
        self.assertEqual(second["config_version"], 7)
        self.assertEqual(first["config_recovery_reason"], "device-version-ahead")
        self.assertNotIn("config_synced_at", first)

    def test_unified_readings_survive_device_deletion(self):
        storage.start_pairing()
        secret = "0123456789abcdef0123456789abcdef"
        device_id = "esp32s3-data01"
        storage.register({"device_id":device_id,"device_secret":secret,"name":"Sensor","device_type":"temperature"}, "127.0.0.1")
        record = storage.approve(device_id)
        storage.record_telemetry(device_id, secret, {"sample_id":"sample-1","data":{"temperature_c":23.4,"humidity_percent":56.2}}, "127.0.0.1")
        packet_id = record["id"]
        detail = storage.get_data_packet(packet_id)
        self.assertEqual({item["id"] for item in detail["categories"]}, {"temperature","humidity"})

        self.assertTrue(storage.delete_device(device_id))
        packets = storage.list_data_packets()
        packet = next(item for item in packets if item["packet_id"] == packet_id)
        self.assertIsNotNone(packet["device_deleted_at"])
        self.assertEqual(packet["record_count"], 2)

        self.assertEqual(storage.delete_data_category(packet_id, "temperature"), 1)
        remaining = storage.get_data_packet(packet_id)
        self.assertEqual([item["id"] for item in remaining["categories"]], ["humidity"])
        self.assertTrue(storage.delete_data_packet(packet_id))
        self.assertIsNone(storage.get_data_packet(packet_id))

    def test_legacy_photo_metadata_migrates(self):
        data = storage._empty()
        data["devices"]["legacy-device"] = {
            "id":"packet-legacy","device_id":"legacy-device","name":"Legacy Camera","device_type":"temperature","status":"approved","created_at":100,
            "config":{},"config_version":0,"secret_hash":storage._hash_secret("0123456789abcdef"),
        }
        storage._save_unlocked(data)
        photo_dir = os.path.join(self.temporary.name, "photos", "legacy-device", "2026-08-30")
        os.makedirs(photo_dir)
        photo_path = os.path.join(photo_dir, "b" * 32 + ".jpg")
        with open(photo_path, "wb") as handle:
            handle.write(_jpeg_bytes())
        legacy = sqlite3.connect(storage.LEGACY_PHOTO_DB_PATH)
        legacy.execute("CREATE TABLE photos(id INTEGER PRIMARY KEY,device_id TEXT,photo_id TEXT,captured_at TEXT,captured_at_source TEXT,received_at INTEGER,width INTEGER,height INTEGER,size_bytes INTEGER,relative_path TEXT)")
        legacy.execute("INSERT INTO photos VALUES(1,?,?,?,?,?,?,?,?,?)", ("legacy-device","b"*32,"2026-08-30T12:00:00.000Z","device",100,800,600,os.path.getsize(photo_path),os.path.relpath(photo_path,self.temporary.name)))
        legacy.commit(); legacy.close()
        storage._load_unlocked()
        detail = storage.get_data_packet("packet-legacy")
        self.assertEqual(detail["categories"][0]["id"], "photo")
        self.assertEqual(detail["categories"][0]["count"], 1)
        self.assertEqual(storage.delete_data_category("packet-legacy", "photo"), 1)
        data_store._MIGRATED.clear()
        storage._load_unlocked()
        self.assertEqual(storage.get_data_packet("packet-legacy")["categories"], [])


if __name__ == "__main__":
    unittest.main()
