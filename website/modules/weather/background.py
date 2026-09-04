import ipaddress
import os
import threading
import time

import geoip2.database
import geoip2.errors
import requests

from modules.weather.store import get_config


GEOIP_DB_PATH = "/home/bbdwz/projects/website/data/geoip/GeoLite2-City.mmdb"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CURRENT_FIELDS = (
    "temperature_2m,apparent_temperature,is_day,precipitation,rain,showers,"
    "snowfall,weather_code,cloud_cover,wind_speed_10m,wind_direction_10m,"
    "wind_gusts_10m,relative_humidity_2m,visibility"
)
WEATHER_CACHE_TTL = 600
WEATHER_CACHE_MAX = 256
GEO_CACHE_MAX = 2048

_LOCK = threading.RLock()
_GEO_READER = None
_GEO_CACHE = {}
_WEATHER_CACHE = {}


def _valid_coordinates(latitude, longitude):
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def _geo_reader():
    global _GEO_READER
    with _LOCK:
        if _GEO_READER is not None:
            return _GEO_READER
        if not os.path.exists(GEOIP_DB_PATH):
            return None
        try:
            _GEO_READER = geoip2.database.Reader(GEOIP_DB_PATH)
        except Exception:
            return None
        return _GEO_READER


def coordinates_from_ip(value):
    ip = str(value or "").split(",", 1)[0].strip()
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if address.is_private or address.is_loopback or address.is_link_local:
        return None

    with _LOCK:
        if ip in _GEO_CACHE:
            return _GEO_CACHE[ip]
    reader = _geo_reader()
    if reader is None:
        return None
    try:
        response = reader.city(ip)
        coords = _valid_coordinates(response.location.latitude, response.location.longitude)
        if not coords:
            return None
        city = ""
        if response.city:
            city = response.city.names.get("zh-CN") or response.city.names.get("en") or ""
        country = ""
        if response.country:
            country = response.country.names.get("zh-CN") or response.country.names.get("en") or ""
        location_name = "，".join(part for part in (country, city) if part)
        result = {"latitude": coords[0], "longitude": coords[1], "location_name": location_name}
    except (geoip2.errors.AddressNotFoundError, ValueError, AttributeError):
        return None
    except Exception:
        return None

    with _LOCK:
        if len(_GEO_CACHE) >= GEO_CACHE_MAX:
            _GEO_CACHE.pop(next(iter(_GEO_CACHE)))
        _GEO_CACHE[ip] = result
    return result


def fallback_coordinates():
    try:
        period = get_config().get("active_period") or {}
        coords = _valid_coordinates(period.get("latitude"), period.get("longitude"))
        if coords:
            return {
                "latitude": coords[0],
                "longitude": coords[1],
                "location_name": str(period.get("location_name") or "默认地区"),
            }
    except Exception:
        pass
    return {"latitude": 52.52, "longitude": 13.405, "location_name": "默认地区"}


def resolve_coordinates(latitude, longitude, client_ip):
    precise = _valid_coordinates(latitude, longitude)
    if precise:
        return {
            "latitude": precise[0],
            "longitude": precise[1],
            "location_name": "当前位置",
            "source": "device",
        }
    approximate = coordinates_from_ip(client_ip)
    if approximate:
        return {**approximate, "source": "ip"}
    return {**fallback_coordinates(), "source": "default"}


def _cache_key(latitude, longitude):
    # Weather grids are much coarser than GPS; rounding also avoids unbounded cache growth.
    return round(float(latitude), 2), round(float(longitude), 2)


def current_weather(latitude, longitude):
    key = _cache_key(latitude, longitude)
    now = time.time()
    with _LOCK:
        cached = _WEATHER_CACHE.get(key)
        if cached and now - cached[0] < WEATHER_CACHE_TTL:
            return cached[1]

    response = requests.get(
        FORECAST_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": CURRENT_FIELDS,
            "timezone": "auto",
        },
        timeout=12,
    )
    response.raise_for_status()
    payload = response.json()
    current = payload.get("current")
    if not isinstance(current, dict) or "weather_code" not in current:
        raise ValueError("Open-Meteo response does not contain current weather")
    result = {
        "current": current,
        "timezone": payload.get("timezone") or "auto",
        "utc_offset_seconds": payload.get("utc_offset_seconds") or 0,
    }
    with _LOCK:
        if len(_WEATHER_CACHE) >= WEATHER_CACHE_MAX:
            oldest_key = min(_WEATHER_CACHE, key=lambda item: _WEATHER_CACHE[item][0])
            _WEATHER_CACHE.pop(oldest_key, None)
        _WEATHER_CACHE[key] = (now, result)
    return result
