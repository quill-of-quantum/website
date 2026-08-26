import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.weather import db
from modules.weather.api import bp
from modules.weather.history import load_historical_weather


class WeatherDatabaseTests(unittest.TestCase):
    def test_legacy_import_and_manual_periods_preserve_readings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "weather.db"
            legacy = root / "number.txt"
            legacy.write_text("2025年09月30日 08:00\n100\n2025年10月01日 08:00\n105\n", encoding="utf-8")
            db.initialize(database)
            self.assertEqual(db.import_legacy_readings(legacy, database), 2)
            self.assertEqual(db.import_legacy_readings(legacy, database), 0)
            original = db.ensure_default_period(database)
            created = db.create_period("2025/26 暖气季", "2025-10-01", database)
            active = next(item for item in db.list_periods(database) if item["active"])
            self.assertEqual(active["id"], created)
            db.activate_period(original["id"], database)
            with db.connection(database) as connection:
                count = connection.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
            self.assertEqual(count, 2)

    def test_location_is_per_period_and_change_clears_only_its_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "weather.db"
            db.initialize(database)
            first = db.create_period("Berlin", "2025-10-01", database, "Berlin", 52.52, 13.405, "Europe/Berlin")
            second = db.create_period("Tokyo", "2026-01-01", database, "Tokyo", 35.68, 139.69, "Asia/Tokyo")
            now = "2026-01-02T00:00:00+01:00"
            with db.connection(database) as connection:
                connection.execute("INSERT INTO weather_daily VALUES(?,?,?,?,?,?,?)", (first, "2025-10-01", 1, 2, 1.5, "archive", now))
                connection.execute("INSERT INTO weather_daily VALUES(?,?,?,?,?,?,?)", (second, "2026-01-01", 3, 4, 3.5, "archive", now))
            db.update_period_location(first, "Hamburg", 53.55, 9.99, "Europe/Berlin", database)
            self.assertEqual(db.weather_cache_counts(first, database)["daily"], 0)
            self.assertEqual(db.weather_cache_counts(second, database)["daily"], 1)

    def test_historical_weather_is_downloaded_only_once(self):
        class FakeResponse:
            def raise_for_status(self): pass
            def json(self):
                hours = [f"2025-01-{day:02d}T{hour:02d}:00" for day in (1, 2) for hour in range(24)]
                return {
                    "hourly": {"time": hours, "temperature_2m": [1.0] * len(hours)},
                    "daily": {"time": ["2025-01-01", "2025-01-02"], "temperature_2m_min": [-1, 0], "temperature_2m_max": [3, 4], "temperature_2m_mean": [1, 2]},
                }
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "weather.db"
            db.initialize(database)
            period_id = db.create_period("History", "2025-01-01", database)
            period = next(item for item in db.list_periods(database) if item["id"] == period_id)
            calls = []
            def fake_get(url, timeout):
                calls.append(url)
                return FakeResponse()
            with patch("modules.weather.db.DB_PATH", database):
                _, _, first = load_historical_weather(period, "2025-01-01", "2025-01-02", fake_get)
                _, _, second = load_historical_weather(period, "2025-01-01", "2025-01-02", fake_get)
            self.assertEqual(len(calls), 1)
            self.assertEqual(first["daily"], 2)
            self.assertEqual(second["daily"], 0)


class WeatherAdminApiTests(unittest.TestCase):
    def setUp(self):
        template_dir = str(Path(__file__).resolve().parents[2] / "templates")
        self.app = Flask(__name__, template_folder=template_dir)
        self.app.secret_key = "test"
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def login(self):
        with self.client.session_transaction() as session:
            session["logged_in"] = True
            session["user"] = "admin"

    def test_admin_api_requires_login(self):
        response = self.client.get("/1/api/weather")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.get_json()["require_login"])

    @patch("modules.weather.api.is_admin_user", return_value=True)
    @patch("modules.weather.api.user_exists", return_value=True)
    @patch("modules.weather.api._service_state", return_value={"active": True})
    @patch("modules.weather.api.latest_run", return_value={"status": "success"})
    @patch("modules.weather.api.get_config", return_value={"homepage_visible": True, "periods": [], "active_period": {}})
    def test_admin_payload_has_no_secret(self, *_mocks):
        self.login()
        payload = self.client.get("/1/api/weather").get_json()
        self.assertTrue(payload["ok"])
        self.assertNotIn("password", str(payload).lower())
        self.assertNotIn("api_key", str(payload).lower())

    @patch("modules.weather.api.is_admin_user", return_value=True)
    @patch("modules.weather.api.user_exists", return_value=True)
    @patch("modules.weather.api._service_state", return_value={"active": True})
    @patch("modules.weather.api.latest_run", return_value={"status": "success"})
    @patch("modules.weather.api.start_analysis")
    def test_manual_run_is_started(self, start_analysis, *_mocks):
        self.login()
        response = self.client.post("/1/api/weather/run", json={})
        self.assertEqual(response.status_code, 200)
        start_analysis.assert_called_once_with("manual")


if __name__ == "__main__":
    unittest.main()
