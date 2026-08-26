from datetime import date, datetime, timedelta
from urllib.parse import urlencode

import pandas as pd
import requests

from modules.weather.db import connection


ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def _date_ranges(days):
    ordered = sorted(set(days))
    if not ordered:
        return []
    ranges, start, previous = [], ordered[0], ordered[0]
    for current in ordered[1:]:
        if current != previous + timedelta(days=1):
            ranges.append((start, previous))
            start = current
        previous = current
    ranges.append((start, previous))
    return ranges


def _request_range(period, start, end, source, http_get):
    params = {
        "latitude": period["latitude"], "longitude": period["longitude"],
        "timezone": period["timezone"], "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": "temperature_2m",
        "daily": "temperature_2m_min,temperature_2m_max,temperature_2m_mean",
    }
    url = (ARCHIVE_URL if source == "archive" else FORECAST_URL) + "?" + urlencode(params)
    response = http_get(url, timeout=20)
    response.raise_for_status()
    payload = response.json()
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    hourly = [
        (period["id"], observed_at, temperature, source, fetched_at)
        for observed_at, temperature in zip(payload.get("hourly", {}).get("time", []), payload.get("hourly", {}).get("temperature_2m", []))
    ]
    daily_payload = payload.get("daily", {})
    daily = [
        (period["id"], observed_on, tmin, tmax, tmean, source, fetched_at)
        for observed_on, tmin, tmax, tmean in zip(
            daily_payload.get("time", []), daily_payload.get("temperature_2m_min", []),
            daily_payload.get("temperature_2m_max", []), daily_payload.get("temperature_2m_mean", []),
        )
    ]
    with connection() as db:
        db.executemany(
            "INSERT INTO weather_hourly(period_id,observed_at,temperature,source,fetched_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(period_id,observed_at) DO UPDATE SET temperature=excluded.temperature,source=excluded.source,fetched_at=excluded.fetched_at",
            hourly,
        )
        db.executemany(
            "INSERT INTO weather_daily(period_id,observed_on,temperature_min,temperature_max,temperature_mean,source,fetched_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(period_id,observed_on) DO UPDATE SET temperature_min=excluded.temperature_min,temperature_max=excluded.temperature_max,temperature_mean=excluded.temperature_mean,source=excluded.source,fetched_at=excluded.fetched_at",
            daily,
        )
    return len(hourly), len(daily)


def load_historical_weather(period, start, end, http_get=requests.get):
    """返回周期历史天气；只请求 SQLite 中缺失的日期。"""
    start, end = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    requested_days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    with connection() as db:
        cached_daily = {
            date.fromisoformat(row["observed_on"])
            for row in db.execute(
                "SELECT observed_on FROM weather_daily WHERE period_id=? AND observed_on BETWEEN ? AND ?",
                (period["id"], start.isoformat(), end.isoformat()),
            )
        }
        hourly_counts = {
            date.fromisoformat(row["day"]): row["count"]
            for row in db.execute(
                "SELECT substr(observed_at,1,10) AS day,COUNT(*) AS count FROM weather_hourly "
                "WHERE period_id=? AND substr(observed_at,1,10) BETWEEN ? AND ? GROUP BY substr(observed_at,1,10)",
                (period["id"], start.isoformat(), end.isoformat()),
            )
        }
    # 夏令时切换日可能只有 23 小时，因此 23 条即视为完整。
    missing = [day for day in requested_days if day not in cached_daily or hourly_counts.get(day, 0) < 23]
    archive_cutoff = datetime.now().astimezone().date() - timedelta(days=5)
    fetched = {"archive_ranges": 0, "forecast_ranges": 0, "hourly": 0, "daily": 0}
    for source, days in (
        ("archive", [day for day in missing if day <= archive_cutoff]),
        ("forecast", [day for day in missing if day > archive_cutoff]),
    ):
        for range_start, range_end in _date_ranges(days):
            hourly_count, daily_count = _request_range(period, range_start, range_end, source, http_get)
            fetched[f"{source}_ranges"] += 1
            fetched["hourly"] += hourly_count
            fetched["daily"] += daily_count
    with connection() as db:
        hourly_rows = db.execute(
            "SELECT observed_at,temperature FROM weather_hourly WHERE period_id=? AND substr(observed_at,1,10) BETWEEN ? AND ? ORDER BY observed_at",
            (period["id"], start.isoformat(), end.isoformat()),
        ).fetchall()
        daily_rows = db.execute(
            "SELECT observed_on,temperature_min,temperature_max,temperature_mean FROM weather_daily WHERE period_id=? AND observed_on BETWEEN ? AND ? ORDER BY observed_on",
            (period["id"], start.isoformat(), end.isoformat()),
        ).fetchall()
    hourly = pd.DataFrame(hourly_rows, columns=["datetime", "temperature"])
    if not hourly.empty:
        hourly["datetime"] = pd.to_datetime(hourly["datetime"])
        hourly = hourly.set_index("datetime")
    daily = pd.DataFrame(daily_rows, columns=["date", "tmin", "tmax", "tavg"])
    if not daily.empty:
        daily["date"] = pd.to_datetime(daily["date"])
        daily = daily.set_index("date")
    return hourly, daily, fetched
