from datetime import timedelta

from modules.situation.api import _parse_event_time


def test_parse_chinese_ios_time_format():
    parsed = _parse_event_time("2026/9/2 GMT+8 11:01:46")

    assert parsed.isoformat() == "2026-09-02T11:01:46+08:00"


def test_parse_english_ios_time_format():
    parsed = _parse_event_time("2026/9/2, 11:01:46 GMT+8")

    assert parsed.isoformat() == "2026-09-02T11:01:46+08:00"


def test_parse_english_ios_time_with_minute_offset():
    parsed = _parse_event_time("2026/9/2, 11:01 GMT-03:30")

    assert parsed.utcoffset() == -timedelta(hours=3, minutes=30)
    assert parsed.second == 0


def test_reject_unknown_time_format():
    assert _parse_event_time("September 2, 2026 11:01") is None
