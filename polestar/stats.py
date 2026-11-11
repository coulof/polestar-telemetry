"""Aggregated weekly figures for the report. Contains no VIN and no coordinates."""
import argparse
import datetime as dt
import json
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
    return week_id(now.date() - dt.timedelta(days=now.weekday() + 7))


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


def weekly(db, wid, now=None):
    """Summary dict for ISO week `wid`, with the previous week's driving/charging for comparison."""
    cfg = config.load()
    tz = ZoneInfo(cfg["report"]["timezone"])
    start = pd.Timestamp(week_start(wid), tz=tz)
    end = start + pd.Timedelta(days=7)
    prev_start = start - pd.Timedelta(days=7)

    battery, odometer = derive.load(db, "battery"), derive.load(db, "odometer")
    location, availability = derive.load(db, "location"), derive.load(db, "availability")
    health, pre = derive.load(db, "health"), derive.load(db, "preCleaning")
    trips = derive.trips(odometer, battery, availability=availability)
    sessions = derive.price_sessions(derive.charge_sessions(battery), location, cfg, battery, manual=costs.load(db))

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
    return {
        "week": wid,
        "period": {"start": str(start.date()), "end": str((end - pd.Timedelta(days=1)).date())},
        "driving": _driving(t),
        "previous_week_driving": _driving(pt),
        "charging": ch,
        "previous_week_charging": {"sessions": int(len(ps)), "kwh": _num(ps["kwh_from_power"].sum()) if len(ps) else 0.0,
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


def main():
    ap = argparse.ArgumentParser(description="Print the weekly stats as JSON")
    ap.add_argument("--week", default="last", help="ISO week like 2026-W46, or 'last'")
    args = ap.parse_args()
    tz = ZoneInfo(config.load()["report"]["timezone"])
    wid = last_full_week(dt.datetime.now(tz)) if args.week == "last" else args.week
    print(json.dumps(weekly(sqlite3.connect(DB_PATH), wid), indent=2))


if __name__ == "__main__":
    main()
