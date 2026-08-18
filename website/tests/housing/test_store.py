import pytest

from modules.housing.store import validate_config


def test_valid_config_keeps_supported_types():
    config = validate_config({
        "incremental_interval_minutes": 15,
        "full_interval_minutes": 120,
        "notify_added_types": ["wg", "invalid", "xzimmer"],
        "notify_delisted_types": ["einzelzimmer"],
        "email_enabled": False,
    })
    assert config["incremental_interval_minutes"] == 15
    assert config["notify_added_types"] == ["wg", "xzimmer"]


@pytest.mark.parametrize("value", [0, 10081, "abc"])
def test_interval_is_validated(value):
    with pytest.raises(ValueError):
        validate_config({"incremental_interval_minutes": value, "full_interval_minutes": 60})


def test_email_address_required_when_notification_is_enabled():
    with pytest.raises(ValueError):
        validate_config({
            "incremental_interval_minutes": 5,
            "full_interval_minutes": 60,
            "email_enabled": True,
            "notification_emails": [],
        })
