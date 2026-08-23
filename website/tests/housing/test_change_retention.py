from datetime import datetime, timedelta, timezone

from modules.housing import db as housing_db


def test_change_label_is_retained_for_eight_hours(tmp_path):
    original_path, original_now = housing_db.DB_PATH, housing_db._now
    start = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)
    current_time = [start]
    housing_db.DB_PATH = tmp_path / "housing.db"
    housing_db._now = lambda: current_time[0].isoformat().replace("+00:00", "Z")
    room = {"42": {
        "id": "42", "rental_type": "wg", "url": "https://example.test/42",
        "summary": "same",
    }}
    try:
        housing_db.apply_catalog(room, "incremental", housing_db._now())
        assert housing_db.list_rooms()[0]["record_change"] == "新上架"

        current_time[0] = start + timedelta(minutes=5)
        changes, _ = housing_db.apply_catalog(room, "incremental", housing_db._now())
        assert changes == []
        assert housing_db.list_rooms()[0]["record_change"] == "新上架"

        current_time[0] = start + timedelta(hours=8, minutes=1)
        housing_db.apply_catalog(room, "incremental", housing_db._now())
        assert housing_db.list_rooms()[0]["record_change"] == "未变化（复用）"
    finally:
        housing_db.DB_PATH, housing_db._now = original_path, original_now


def test_delisted_label_expires_during_a_later_search(tmp_path):
    original_path, original_now = housing_db.DB_PATH, housing_db._now
    start = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)
    current_time = [start]
    housing_db.DB_PATH = tmp_path / "housing.db"
    housing_db._now = lambda: current_time[0].isoformat().replace("+00:00", "Z")
    room = {"42": {"id": "42", "rental_type": "wg", "url": "https://example.test/42", "summary": "same"}}
    try:
        housing_db.apply_catalog(room, "incremental", housing_db._now())
        current_time[0] = start + timedelta(minutes=1)
        housing_db.apply_catalog({}, "incremental", housing_db._now())
        delisted = housing_db.list_rooms()[0]
        assert delisted["record_change"] == "已下架"
        assert delisted["listing_duration_seconds"] == 60
        current_time[0] = start + timedelta(hours=8, minutes=2)
        housing_db.apply_catalog({}, "incremental", housing_db._now())
        assert housing_db.list_rooms()[0]["record_change"] == "未变化（复用）"
    finally:
        housing_db.DB_PATH, housing_db._now = original_path, original_now
