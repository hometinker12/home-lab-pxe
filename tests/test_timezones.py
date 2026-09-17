from src.timezones import is_valid_timezone, list_timezones, timezone_choices


def test_timezone_choices_put_utc_first_and_keep_unknown():
    zones = list_timezones()
    assert "UTC" in zones
    assert is_valid_timezone("UTC")
    assert is_valid_timezone("America/New_York")
    assert not is_valid_timezone("Not/AZone")
    assert not is_valid_timezone("localtime")
    choices = timezone_choices("UTC")
    assert choices[0] == "UTC"
    extra = timezone_choices("Made/Up")
    assert extra[0] == "Made/Up"
    assert "UTC" in extra
