import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.weather import background
from modules.weather.api import bp


class WeatherBackgroundTests(unittest.TestCase):
    def setUp(self):
        background._WEATHER_CACHE.clear()
        background._GEO_CACHE.clear()
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    @patch("modules.weather.api.current_weather")
    @patch("modules.weather.api.resolve_coordinates")
    def test_device_coordinates_are_forwarded_to_weather_service(self, resolve, weather):
        resolve.return_value = {
            "latitude": 48.13, "longitude": 11.58,
            "location_name": "当前位置", "source": "device",
        }
        weather.return_value = {
            "current": {"weather_code": 61, "is_day": 1},
            "timezone": "Europe/Berlin", "utc_offset_seconds": 7200,
        }
        response = self.client.get("/api/weather/background?lat=48.13&lon=11.58")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["source"], "device")
        self.assertEqual(len(response.get_json()["network_key"]), 16)
        resolve.assert_called_once_with("48.13", "11.58", "127.0.0.1")
        weather.assert_called_once_with(48.13, 11.58)

    @patch("modules.weather.background.coordinates_from_ip")
    def test_ip_location_is_used_when_device_location_is_absent(self, lookup):
        lookup.return_value = {
            "latitude": 52.52, "longitude": 13.405, "location_name": "德国，柏林",
        }
        result = background.resolve_coordinates(None, None, "203.0.113.10")
        self.assertEqual(result["source"], "ip")
        self.assertEqual(result["location_name"], "德国，柏林")

    @patch("modules.weather.background.requests.get")
    def test_weather_result_is_cached(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "current": {"weather_code": 2, "is_day": 1},
            "timezone": "Europe/Berlin",
            "utc_offset_seconds": 7200,
        }
        get.return_value = response

        first = background.current_weather(52.52, 13.405)
        second = background.current_weather(52.52, 13.405)

        self.assertEqual(first, second)
        self.assertEqual(get.call_count, 1)
        self.assertIn("visibility", get.call_args.kwargs["params"]["current"])


if __name__ == "__main__":
    unittest.main()
