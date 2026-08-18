import json
from datetime import datetime, timezone

from modules.housing import db as housing_db


def test_initialization_preserves_geocode_and_creates_quiet_baseline(tmp_path):
    original_path, original_now = housing_db.DB_PATH, housing_db._now
    housing_db.DB_PATH = tmp_path / "housing.db"
    housing_db._now = lambda: datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    room = {"42": {
        "id": "42", "rental_type": "wg", "url": "https://example.test/42",
        "summary": "20 m² WG",
    }}
    try:
        housing_db.put_geocode_cache("Kaiserstr. 1", {"lat": 49.0, "lon": 8.4})
        housing_db.apply_catalog(room, "incremental", housing_db._now())
        housing_db.reset_tracking_data()
        cached, result = housing_db.get_geocode_cache("Kaiserstr. 1")
        assert cached and result["lat"] == 49.0
        changes, counts = housing_db.apply_catalog(room, "full", housing_db._now(), baseline=True)
        assert changes == [] and counts == {"added": 0, "updated": 0, "delisted": 0}
        saved = housing_db.list_rooms()[0]
        assert saved["record_change"] == "未变化（复用）"
        with housing_db.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 0
            assert connection.execute("SELECT mode FROM runs").fetchone()[0] == "initialize"
    finally:
        housing_db.DB_PATH, housing_db._now = original_path, original_now
