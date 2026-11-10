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
