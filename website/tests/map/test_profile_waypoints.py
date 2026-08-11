import importlib.util
from pathlib import Path


API_PATH = Path(__file__).parents[2] / "modules" / "map" / "api.py"
SPEC = importlib.util.spec_from_file_location("map_api_for_test", API_PATH)
MAP_API = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MAP_API)


def test_build_profile_waypoints_adds_markers_and_segment_durations():
    route_data = {
        "duration": 1200,
        "steps": [{
            "duration": 1200,
            "path": "116.0000,40.0000;116.0100,40.0000;116.0200,40.0000"
        }]
    }
    waypoints = [{"coords": "40.0000,116.0100", "name": "检索名称"}]
    profile = [
        {"distance_km": 0.0, "ele": 10},
        {"distance_km": 0.85, "ele": 120},
        {"distance_km": 1.70, "ele": 20}
    ]

    markers, segments = MAP_API.build_profile_waypoints(
        route_data, waypoints, ["测试途经地"], profile
    )

    assert markers == [{"name": "检索名称", "distance_km": 0.85, "ele": 120}]
    assert len(segments) == 2
    assert 0.84 <= segments[0]["distance_km"] <= 0.86
    assert 0.84 <= segments[1]["distance_km"] <= 0.86
    assert 9.9 <= segments[0]["duration_min"] <= 10.1
    assert 9.9 <= segments[1]["duration_min"] <= 10.1
