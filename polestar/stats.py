"""Aggregated figures for the Claude reports (week, month, quarter, year). Contains no VIN and no coordinates.

Period ids: week 2026-W47, month 2026-11, quarter 2026-Q4, year 2026."""
import argparse
import datetime as dt
import json
import re
import sqlite3
from zoneinfo import ZoneInfo

import pandas as pd

from . import charging, config, costs, derive, insights
from .collect import DB_PATH


def week_id(day):
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_start(wid):
    year, week = wid.split("-W")
    return dt.date.fromisocalendar(int(year), int(week), 1)


def last_full_week(now):
    """ISO week id of the most recent Monday-to-Sunday week that has ended."""
    return last_full("week", now)


KINDS = ("week", "month", "quarter", "year")
_PATTERNS = {"week": r"\d{4}-W\d{2}", "month": r"\d{4}-\d{2}", "quarter": r"\d{4}-Q[1-4]", "year": r"\d{4}"}


def kind_of(pid):
    for kind, pat in _PATTERNS.items():
        if re.fullmatch(pat, pid):
            return kind
    raise ValueError(f"not a period id: {pid!r}")


def bounds(pid):
    """(kind, first local day, first day after the period) for a period id."""
    kind = kind_of(pid)
    if kind == "week":
        start = week_start(pid)
        return kind, start, start + dt.timedelta(days=7)
    year = int(pid[:4])
    if kind == "year":
        return kind, dt.date(year, 1, 1), dt.date(year + 1, 1, 1)
    first = int(pid[5:]) if kind == "month" else 3 * int(pid[-1]) - 2
    months = 1 if kind == "month" else 3
    start = dt.date(year, first, 1)
    nxt = first + months
    return kind, start, dt.date(year + (nxt - 1) // 12, (nxt - 1) % 12 + 1, 1)


def period_id(kind, day):
    """Id of the period of `kind` that contains `day`."""
    if kind == "week":
        return week_id(day)
    if kind == "month":
        return f"{day.year}-{day.month:02d}"
    if kind == "quarter":
        return f"{day.year}-Q{(day.month - 1) // 3 + 1}"
    return str(day.year)


def previous(pid):
    kind, start, _ = bounds(pid)
    return period_id(kind, start - dt.timedelta(days=1))


def last_full(kind, now):
    """Id of the most recent period of `kind` that has ended at `now`."""
    return previous(period_id(kind, now.date()))


def label(pid):
    """Readable name: 'Week 47 · 16–22 Nov 2026', 'November 2026', 'Q4 2026', '2026'."""
    kind, start, end = bounds(pid)
    last = end - dt.timedelta(days=1)
    if kind == "week":
        span = f"{start.day}–{last:%-d %b %Y}" if start.month == last.month else f"{start:%-d %b}–{last:%-d %b %Y}"
        return f"Week {int(pid[-2:])} · {span}"
    if kind == "month":
        return f"{start:%B %Y}"
    return f"{pid[-2:]} {pid[:4]}" if kind == "quarter" else pid


def _local(df, col, tz):
    return df[col].dt.tz_convert(tz)


def _in(df, col, start, end, tz):
    if df.empty:
        return df
    t = _local(df, col, tz)
    return df[(t >= start) & (t < end)]


def _num(x, digits=1):
    return None if x is None or pd.isna(x) else round(float(x), digits)


def _driving(trips):
    km = trips["km"].sum() if len(trips) else 0.0
    return {
        "trips": int(len(trips)), "km": _num(km),
        "kwh_per_100km_estimated": _num(derive.avg_consumption(trips)),
        "longest_trip_km": _num(trips["km"].max()) if len(trips) else None,
        "avg_speed_kmh": _num(km / (trips["minutes"].sum() / 60)) if len(trips) and trips["minutes"].sum() > 0 else None,
    }


def _charging(sessions):
    if sessions.empty:
        return {"sessions": 0, "kwh": 0.0, "cost_eur": None, "peak_kw": None, "by_location": [], "partial_sessions": 0}
    by_loc = []
    for name, g in sessions.assign(location=sessions["location"].fillna("unpriced place")).groupby("location"):
        by_loc.append({"name": name, "sessions": int(len(g)), "kwh": _num(g["kwh_from_power"].sum()),
                       "cost_eur": _num(g["cost"].sum(min_count=1), 2)})
    return {
        "sessions": int(len(sessions)), "kwh": _num(sessions["kwh_from_power"].sum()),
        "cost_eur": _num(sessions["cost"].sum(min_count=1), 2), "peak_kw": _num(sessions["peak_kw"].max()),
        "by_location": by_loc,
        "types": sessions["type"].dropna().value_counts().to_dict(),
        "partial_sessions": int(sessions["partial"].sum()),
        "potential_saving_eur": _num(sessions["saving"].sum(min_count=1), 2),   # peak energy at the cheapest rate
    }


def summary(db, pid):
    """Summary dict for period `pid` (week, month, quarter or year), with the previous period's driving and
    charging for comparison, and a month-by-month breakdown for quarters and years."""
    cfg = config.load()
    tz = ZoneInfo(cfg["report"]["timezone"])
    kind, first, after = bounds(pid)
    start, end = pd.Timestamp(first, tz=tz), pd.Timestamp(after, tz=tz)
    prev_start = pd.Timestamp(bounds(previous(pid))[1], tz=tz)

    battery, odometer = derive.load(db, "battery"), derive.load(db, "odometer")
    location, availability = derive.load(db, "location"), derive.load(db, "availability")
    health, pre = derive.load(db, "health"), derive.load(db, "preCleaning")
    trips = derive.trips(odometer, battery, availability=availability)
    sessions = derive.price_sessions(derive.charge_sessions(battery), location, cfg, battery, manual=costs.load(db),
                                     locations=costs.load_locations(db))

    def window(a, b):
        return _in(trips, "start", a, b, tz), _in(sessions, "start", a, b, tz)

    t, s = window(start, end)
    pt, ps = window(prev_start, start)
    batt_w = _in(battery, "time", start, end, tz)
    soc = pd.to_numeric(batt_w.get("batteryChargeLevelPercentage"), errors="coerce") if not batt_w.empty else pd.Series(dtype=float)
    drain = derive.idle_drain(batt_w, t) if not batt_w.empty else pd.DataFrame(columns=["soc_lost_pct"])
    cap = insights.capacity_trend(s)
    rr = insights.real_range(t)
    air = derive.air_quality(pre)
    air_w = _in(air, "time", start, end, tz)

    service = None
    if not health.empty:
        h = health.iloc[-1]
        d, km = h.get("daysToService"), h.get("distanceToServiceKm")
        if pd.notna(d) and pd.notna(km):
            service = {"days": int(d), "km": int(km), "status": config.service_status(cfg, d, km)}

    notes = []
    if t.empty:
        notes.append("No trips detected this week.")
    if len(s) and s["partial"].any():
        notes.append("Some charge sessions began before tracking started; their energy is a lower bound.")
    notes.append("Energy figures are estimates (battery capacity assumed, charger-side power).")

    ch = _charging(s)
    amp = charging.amp_limit(charging.latest_payload(db, "ampLimit"))
    amps = pd.Series(dtype=float)
    if not batt_w.empty and "chargingCurrentAmps" in batt_w and "chargingStatusV2" in batt_w:
        on = batt_w["chargingStatusV2"].isin(derive.CHARGING_STATES)
        amps = pd.to_numeric(batt_w.loc[on, "chargingCurrentAmps"], errors="coerce").dropna()
    ch["amp_limit_in_car_a"] = _num(amp, 0)
    ch["median_charging_current_a"] = _num(amps.median(), 0) if len(amps) else None
    out = {
        "period_id": pid,
        "kind": kind,
        "period": {"start": str(start.date()), "end": str((end - pd.Timedelta(days=1)).date())},
        "driving": _driving(t),
        "previous_period_driving": _driving(pt),
        "charging": ch,
        "previous_period_charging": {"sessions": int(len(ps)), "kwh": _num(ps["kwh_from_power"].sum()) if len(ps) else 0.0,
                                   "cost_eur": _num(ps["cost"].sum(min_count=1), 2) if len(ps) else None},
        "battery": {
            "soc_min_pct": _num(soc.min(), 0) if len(soc) else None, "soc_max_pct": _num(soc.max(), 0) if len(soc) else None,
            "parked_drain_pct": _num(drain["soc_lost_pct"].sum()) if len(drain) else None,
            "energy_per_full_charge_kwh": _num(cap["capacity_kwh"].median(), 0) if len(cap) else None,
            "real_world_range_km": _num(rr["range_100_km"], 0) if rr else None,
        },
        "service": service,
        "air": {"readings": int(len(air_w)), "aqi_avg": _num(air_w["aqi"].mean()) if len(air_w) else None,
                "aqi_max": _num(air_w["aqi"].max(), 0) if len(air_w) else None,
                "pm25_avg": _num(air_w["pm25"].mean()) if len(air_w) else None,
                "pm25_max": _num(air_w["pm25"].max(), 0) if len(air_w) else None},
        "data_quality": {"battery_events": int(len(batt_w)), "notes": notes},
    }
    if kind in ("quarter", "year"):
        m = insights.monthly(s, t, tz=tz)
        out["by_month"] = [{"month": r["month"], "km": _num(r["km"]), "kwh": _num(r["kwh"]),
                            "cost_eur": _num(r["cost"], 2), "sessions": int(r["sessions"]) if pd.notna(r["sessions"]) else 0}
                           for _, r in m.iterrows()]
    return out


def main():
    ap = argparse.ArgumentParser(description="Print a period's stats as JSON")
    ap.add_argument("--period", default="last", help="period id (2026-W46, 2026-11, 2026-Q4, 2026), or 'last' (week)")
    args = ap.parse_args()
    tz = ZoneInfo(config.load()["report"]["timezone"])
    pid = last_full_week(dt.datetime.now(tz)) if args.period == "last" else args.period
    print(json.dumps(summary(sqlite3.connect(DB_PATH), pid), indent=2))


if __name__ == "__main__":
    main()
