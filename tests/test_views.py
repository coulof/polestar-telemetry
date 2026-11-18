import pandas as pd
import pytest

from polestar import config, views


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "none.toml"))
    return config.load()


def df(**cols):
    return pd.DataFrame({k: [v] for k, v in cols.items()})


def test_rgba():
    assert views.rgba("#ED760E", 0.5) == "rgba(237,118,14,0.5)"


def test_chips_locked_closed_service_and_air(cfg):
    ext = df(centralLock="LOCK_STATUS_LOCKED", hood="OPEN_STATUS_CLOSED")
    health = df(daysToService=200, distanceToServiceKm=9000)
    pre = df(measuredAirQualityIndex=14, measuredParticulateMatter25=4)
    line, warn = views.chips(ext, health, pre, cfg)
    assert "Locked" in line and "All closed" in line and "Service in 200 d / 9,000 km" in line and "Air good" in line and "PM2.5 4" in line
    assert warn is None


def test_chips_open_parts_are_named_and_service_warning_is_raised(cfg):
    ext = df(centralLock="LOCK_STATUS_UNLOCKED", frontLeftDoor="OPEN_STATUS_OPEN", tailgate="OPEN_STATUS_OPEN")
    line, warn = views.chips(ext, df(daysToService=5, distanceToServiceKm=100), pd.DataFrame(), cfg)
    assert "Unlocked" in line and "front left door, tailgate" in line
    assert warn and "Service due in 5 days" in warn


def test_chips_while_driving_hide_stale_lock_state(cfg):
    line, _ = views.chips(df(centralLock="LOCK_STATUS_LOCKED"), pd.DataFrame(), pd.DataFrame(), cfg, driving=True)
    assert "In use" in line and "Locked" not in line


def test_chip_text_is_html_escaped():
    from polestar import theme
    assert "<script>" not in theme.chip("<script>alert(1)</script>", False)
    assert "&lt;script&gt;" in theme.chip("<script>", False)


def test_distance_chart_stacks_trips_and_labels_only_the_longest_day_and_today():
    tz = "Europe/Paris"
    trips = pd.DataFrame({
        "start": pd.to_datetime(["2026-11-10 08:00", "2026-11-10 17:30", "2026-11-11 09:00", "2026-11-12 07:45"]).tz_localize(tz),
        "km": [12.0, 40.0, 5.0, 8.0], "minutes": [20.0, 45.0, 10.0, 15.0], "kwh": [2.0, None, None, 1.5],
        "frm": ["Home", "Office", None, "Home"], "to": ["Office", "Home", None, "Gym"]})
    xrange = [pd.Timestamp("2026-11-09 12:00", tz=tz), pd.Timestamp("2026-11-12 12:00", tz=tz)]
    fig = views.distance_chart(trips, xrange)
    assert fig.layout.barmode == "stack" and len(fig.data) == 2          # at most two trips on one day
    labels = [a.text for a in fig.layout.annotations]
    assert "52 km" in labels and "8 km" in labels and "5 km" not in labels   # longest day and today only
    assert any(a.startswith("avg 16 km/day") for a in labels)            # 65 km over 4 calendar days
    first = fig.data[0].customdata[0]
    assert first[0].startswith("<b>Tue 10 Nov · 52 km</b> · 2 trips")
    assert first[1] == "08:00 Home → Office · 12 km · 20 min · ~2.0 kWh"
    assert fig.data[0].customdata[1][1] == "09:00 · 5 km · 10 min"       # places unknown: time only
