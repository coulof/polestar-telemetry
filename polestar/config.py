"""Optional config.toml: poll intervals and priced locations (see config.example.toml)."""
import datetime as dt
import math
import os
import tomllib

DEFAULT_PATH = "config.toml"
DEFAULTS = {"interval": 300, "interval_active": 60, "radius_m": 150}
REPORT_DEFAULTS = {"enabled": True, "timezone": "Europe/Paris", "model": "sonnet"}
SERVICE_DEFAULTS = {"warn_days": 30, "warn_km": 1500}
ALERT_DEFAULTS = {"stale_hours": 6}
AIR_DEFAULTS = {"good": 5, "moderate": 15}   # PM2.5 limits (assumed ug/m3), after the WHO 2021 annual and 24-hour guidelines


def load(path=None):
    path = path or os.environ.get("CONFIG_PATH", DEFAULT_PATH)
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raw = {}
    poll = {**DEFAULTS, **raw.get("poll", {})}
    tz = raw.get("timezone") or raw.get("report", {}).get("timezone") or REPORT_DEFAULTS["timezone"]
    return {
        "timezone": tz,
        "interval": int(poll["interval"]),
        "interval_active": int(poll["interval_active"]),
        "report": {**REPORT_DEFAULTS, **raw.get("report", {}), "timezone": tz},
        "co2_g_per_kwh": raw.get("co2", {}).get("g_per_kwh"),
        "service": {**SERVICE_DEFAULTS, **raw.get("service", {})},
        "alerts": {**ALERT_DEFAULTS, **raw.get("alerts", {})},
        "air": {**AIR_DEFAULTS, **raw.get("air", {})},
        "default_price": raw.get("pricing", {}).get("default_price_per_kwh"),
        "locations": [
            {"radius_m": DEFAULTS["radius_m"], **loc} for loc in raw.get("location", [])
        ],
    }


def _distance_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


def match(cfg, lat, lon):
    """The nearest configured location whose radius contains the point, else None."""
    if lat is None or lon is None or lat != lat or lon != lon:
        return None
    best = None
    for loc in cfg["locations"]:
        d = _distance_m(lat, lon, loc["lat"], loc["lon"])
        if d <= loc["radius_m"] and (best is None or d < best[0]):
            best = (d, loc)
    return best[1] if best else None


def match_location(cfg, lat, lon):
    """(name, price per kWh) of the matched location, else (None, default price). Price None = unknown."""
    loc = match(cfg, lat, lon)
    return (loc["name"], loc["price_per_kwh"]) if loc else (None, cfg["default_price"])


def _minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _applies(tariff, local_dt):
    """A tariff with `days = ["sat", "sun"]` only applies on those weekdays (of the instant's local date)."""
    days = tariff.get("days")
    return not days or local_dt.weekday() in {WEEKDAYS[d.lower()[:3]] for d in days}


def price_at(loc, local_dt):
    """Price per kWh at a local datetime: the first matching [[location.tariff]], else the base price.

    A window like from = "23:00", to = "07:00" crosses midnight; from = to means the whole day."""
    now = local_dt.hour * 60 + local_dt.minute
    for t in loc.get("tariff", []):
        a, b = _minutes(t["from"]), _minutes(t["to"])
        if _applies(t, local_dt) and ((a <= now < b) if a < b else (now >= a or now < b)):
            return t["price_per_kwh"]
    return loc["price_per_kwh"]


def cheapest_price(loc, local_dt=None):
    """Lowest price per kWh on offer: the base price or any tariff.

    With `local_dt`, only tariffs that apply on that weekday count, so a weekend-only rate is not offered
    as an alternative to a weekday charge (you cannot move a Tuesday charge to Saturday)."""
    prices = [loc["price_per_kwh"]] + [t["price_per_kwh"] for t in loc.get("tariff", [])
                                      if local_dt is None or _applies(t, local_dt)]
    return min(prices)


def next_cheaper_start(loc, local_dt):
    """Start (local datetime) of a cheaper rate beginning within the next 24 hours, or None."""
    now_price = price_at(loc, local_dt)
    best = None
    for t in loc.get("tariff", []):
        h, m = divmod(_minutes(t["from"]), 60)
        for add in (0, 1):
            cand = (local_dt + dt.timedelta(days=add)).replace(hour=h, minute=m, second=0, microsecond=0)
            if local_dt < cand <= local_dt + dt.timedelta(hours=24) and price_at(loc, cand) < now_price:
                if best is None or cand < best:
                    best = cand
    return best


def service_status(cfg, days, km):
    """'ok' | 'soon' (within the warn thresholds) | 'overdue', for whichever limit is closest."""
    if (days is not None and days <= 0) or (km is not None and km <= 0):
        return "overdue"
    soon = cfg["service"]
    if (days is not None and days <= soon["warn_days"]) or (km is not None and km <= soon["warn_km"]):
        return "soon"
    return "ok"


def air_status(cfg, pm25):
    """'good' | 'moderate' | 'high' for a PM2.5 reading against the [air] limits; None without a reading."""
    if pm25 is None or pm25 != pm25:
        return None
    return "good" if pm25 <= cfg["air"]["good"] else "moderate" if pm25 <= cfg["air"]["moderate"] else "high"
