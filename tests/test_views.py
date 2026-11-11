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
    assert "Locked" in line and "All closed" in line and "Service in 200 d / 9,000 km" in line and "Air quality 14" in line
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
