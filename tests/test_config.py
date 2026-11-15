from polestar import config


def test_defaults_without_file(tmp_path):
    cfg = config.load(tmp_path / "missing.toml")
    assert cfg["interval"] == 300 and cfg["interval_active"] == 60 and cfg["locations"] == []


def test_load_and_nearest_match(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("""
[poll]
interval = 120
[[location]]
name = "A"
lat = 50.0
lon = 3.0
price_per_kwh = 0.1
""")
    cfg = config.load(p)
    assert cfg["interval"] == 120 and cfg["interval_active"] == 60
    assert config.match_location(cfg, 50.0005, 3.0) == ("A", 0.1)   # ~55 m
    assert config.match_location(cfg, 50.01, 3.0) == (None, None)    # ~1.1 km


def test_service_status(tmp_path):
    cfg = config.load(tmp_path / "missing.toml")
    assert config.service_status(cfg, 507, 23803) == "ok"
    assert config.service_status(cfg, 20, 23803) == "soon"
    assert config.service_status(cfg, 507, 1200) == "soon"
    assert config.service_status(cfg, 0, 5000) == "overdue"


def test_air_status_uses_the_configured_limits(tmp_path):
    cfg = config.load(str(tmp_path / "none.toml"))
    assert [config.air_status(cfg, v) for v in (0, 5, 6, 15, 16, None, float("nan"))] == ["good", "good", "moderate", "moderate", "high", None, None]
    (tmp_path / "c.toml").write_text("[air]\ngood = 10\n")
    assert config.air_status(config.load(str(tmp_path / "c.toml")), 8) == "good"
