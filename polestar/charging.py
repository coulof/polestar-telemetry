"""Charging intelligence: the car's charging settings and exception checks (amp limit, plug state).

Settings come from the `pdp-charging/*` endpoints (stored as snapshots like everything else)."""
import datetime as dt
import json

def latest_payload(db, domain):
    row = db.execute("SELECT payload FROM snapshots WHERE domain = ? ORDER BY ts DESC LIMIT 1", (domain,)).fetchone()
    return json.loads(row[0]) if row else None


def target_soc(payload):
    return ((payload or {}).get("targetSoc") or {}).get("batteryChargeTargetLevel")


def amp_limit(payload):
    return ((payload or {}).get("ampLimit") or {}).get("ampLimit")


def _hm(t):
    return int(t.get("hour") or 0), int(t.get("minute") or 0)     # minute is omitted when 0


def _tz(t):
    off = (t.get("timeZone") or {}).get("offsetMinutes")
    return dt.timezone(dt.timedelta(minutes=off)) if off is not None else None


def next_timer_start(charge_timer, now):
    """Start of the next window of an activated global charge timer, or None."""
    gt = (charge_timer or {}).get("globalChargeTimer") or {}
    if not gt.get("activated") or not gt.get("start"):
        return None
    tz = _tz(gt["start"]) or now.tzinfo
    h, m = _hm(gt["start"])
    local = now.astimezone(tz)
    for i in range(2):
        d = (local + dt.timedelta(days=i)).date()
        cand = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
        if cand > now:
            return cand
    return None


def amp_check(limit_a, actual_a, tolerance=0.85):
    """(limit, actual) when charging well below the amp limit set in the car, else None."""
    if limit_a and actual_a and actual_a < limit_a * tolerance:
        return float(limit_a), float(actual_a)
    return None


def plug_issue(b, target, timer_start):
    """Exception while plugged in, as (level, text), or None when all is as expected.

    Waiting for a schedule is normal ('info'); idle below target with no schedule is not ('warn')."""
    conn = b.get("chargerConnectionStatus")
    status = str(b.get("chargingStatusV2", ""))
    if conn == "CHARGER_CONNECTION_STATUS_FAULT":
        return "error", "Charger connection fault"
    if conn != "CHARGER_CONNECTION_STATUS_CONNECTED":
        return None
    if status.endswith("_ERROR"):
        return "error", "Charging error reported by the car"
    if "SCHEDULED" in status or "SMART_CHARGING" in status:
        return "info", "Waiting for the charge schedule" + (f", starts {timer_start:%H:%M}" if timer_start else "")
    soc = b.get("batteryChargeLevelPercentage")
    if status.endswith("_IDLE") and soc is not None and target is not None and soc < target - 1:
        if timer_start:
            return "info", f"Waiting for the charge timer, starts {timer_start:%H:%M}"
        return "warn", f"Plugged in but not charging ({soc:.0f} % of the {target:.0f} % target)"
    return None
