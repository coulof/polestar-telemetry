"""Optional config.toml: poll intervals and priced locations (see config.example.toml)."""
import math
import os
import tomllib

DEFAULT_PATH = "config.toml"
DEFAULTS = {"interval": 300, "interval_active": 60, "radius_m": 150}
SERVICE_DEFAULTS = {"warn_days": 30, "warn_km": 1500}


def load(path=None):
    path = path or os.environ.get("CONFIG_PATH", DEFAULT_PATH)
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raw = {}
    poll = {**DEFAULTS, **raw.get("poll", {})}
    return {
        "interval": int(poll["interval"]),
        "interval_active": int(poll["interval_active"]),
        "co2_g_per_kwh": raw.get("co2", {}).get("g_per_kwh"),
        "service": {**SERVICE_DEFAULTS, **raw.get("service", {})},
        "default_price": raw.get("pricing", {}).get("default_price_per_kwh"),
        "locations": [
            {"radius_m": DEFAULTS["radius_m"], **loc} for loc in raw.get("location", [])
        ],
    }


def _distance_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


def match_location(cfg, lat, lon):
    """(name, price per kWh) of the nearest configured location containing the point,
    else (None, default price). Price None means unknown."""
    if lat is None or lon is None or lat != lat or lon != lon:
        return None, cfg["default_price"]
    best = None
    for loc in cfg["locations"]:
        d = _distance_m(lat, lon, loc["lat"], loc["lon"])
        if d <= loc["radius_m"] and (best is None or d < best[0]):
            best = (d, loc)
    if best:
        return best[1]["name"], best[1]["price_per_kwh"]
    return None, cfg["default_price"]


def service_status(cfg, days, km):
    """'ok' | 'soon' (within the warn thresholds) | 'overdue', for whichever limit is closest."""
    if (days is not None and days <= 0) or (km is not None and km <= 0):
        return "overdue"
    soon = cfg["service"]
    if (days is not None and days <= soon["warn_days"]) or (km is not None and km <= soon["warn_km"]):
        return "soon"
    return "ok"
