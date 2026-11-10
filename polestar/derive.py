"""Derive trips and charge sessions from stored snapshots.

Snapshots are sparse and event-driven, so everything here is an estimate:
energy comes from SoC deltas x battery capacity (BATTERY_KWH) or from
integrating reported charging power.
"""
import json
import os
import sqlite3

import pandas as pd

BATTERY_KWH = float(os.environ.get("BATTERY_KWH", 78))  # usable; unverified for this variant
TRIP_GAP_S = 15 * 60       # odometer readings further apart than this start a new trip
CHARGE_GAP_S = 2 * 3600    # charging readings further apart than this start a new session
CHARGING_STATES = {
    "CHARGING_STATUS_V2_CHARGING",
    "CHARGING_STATUS_V2_CHARGING_IS_EN_ROUTE",
    "CHARGING_STATUS_V2_CHARGING_TOWARDS_MIN_SOC",
    "CHARGING_STATUS_V2_SMART_CHARGING",
}


def load(db, domain):
    """Snapshots of one domain as a DataFrame (payload keys flattened), sorted by time."""
    rows = db.execute(
        "SELECT ts, payload FROM snapshots WHERE domain = ? ORDER BY ts", (domain,)
    ).fetchall()
    df = pd.json_normalize([json.loads(p) for _, p in rows])
    if df.empty:
        return df
    df.insert(0, "time", pd.to_datetime([t for t, _ in rows], unit="s", utc=True))
    return df


def _asof(df, times, cols, direction="backward"):
    """Nearest value of `cols` at/before (or at/after) each time, within 1h (NaN if none)."""
    if df.empty:
        return pd.DataFrame({c: [float("nan")] * len(times) for c in cols})
    left = pd.DataFrame({"time": pd.to_datetime(times, utc=True)})
    out = pd.merge_asof(
        left, df[["time", *cols]], on="time", direction=direction, tolerance=pd.Timedelta("1h")
    )
    return out[cols]


def trips(odometer, battery, location=None):
    """One row per trip: a run of increasing odometer readings with no big gaps."""
    cols = ["start", "end", "km", "minutes", "avg_kmh", "soc_start", "soc_end", "kwh", "kwh_per_100km"]
    if odometer.empty or "odometerMeters" not in odometer:
        return pd.DataFrame(columns=cols)
    o = odometer.dropna(subset=["odometerMeters"]).sort_values("time").reset_index(drop=True)
    o["km_total"] = o["odometerMeters"] / 1000
    moved = o["km_total"].diff() > 0
    gap = o["time"].diff().dt.total_seconds() > TRIP_GAP_S
    # A reading belongs to a trip if the odometer rose since the previous reading;
    # the trip starts at the previous reading (where the car was last stationary).
    trip_id = (~moved | gap).cumsum()
    rows = []
    for _, g in o[moved & ~gap].groupby(trip_id[moved & ~gap]):
        first = o.loc[g.index[0] - 1]
        last = g.iloc[-1]
        rows.append({"start": first["time"], "end": last["time"], "km": last["km_total"] - first["km_total"]})
    t = pd.DataFrame(rows, columns=["start", "end", "km"])
    if t.empty:
        return pd.DataFrame(columns=cols)
    t["minutes"] = (t["end"] - t["start"]).dt.total_seconds() / 60
    t["avg_kmh"] = t["km"] / (t["minutes"] / 60)
    b = battery.dropna(subset=["batteryChargeLevelPercentage"]) if not battery.empty else battery
    t["soc_start"] = _asof(b, t["start"], ["batteryChargeLevelPercentage"]).to_numpy().ravel()
    t["soc_end"] = _asof(b, t["end"], ["batteryChargeLevelPercentage"], "forward").to_numpy().ravel()
    t["kwh"] = (t["soc_start"] - t["soc_end"]) / 100 * BATTERY_KWH
    t["kwh_per_100km"] = t["kwh"] / t["km"] * 100
    return t[cols]


def charge_sessions(battery):
    """One row per charging session (consecutive charging readings)."""
    cols = ["start", "end", "minutes", "soc_start", "soc_end", "kwh_from_soc", "kwh_from_power", "peak_kw", "type"]
    if battery.empty or "chargingStatusV2" not in battery:
        return pd.DataFrame(columns=cols)
    b = battery.sort_values("time").reset_index(drop=True)
    charging = b["chargingStatusV2"].isin(CHARGING_STATES)
    # A session ends at the first non-charging reading, or after a long silence.
    gap = b["time"].diff().dt.total_seconds() > CHARGE_GAP_S
    sid = (charging.ne(charging.shift()) | gap).cumsum()
    rows = []
    for _, g in b[charging].groupby(sid[charging]):
        hours = g["time"].diff().dt.total_seconds().fillna(0) / 3600
        power = pd.to_numeric(g.get("chargingPowerWatts"), errors="coerce").fillna(0) / 1000
        kwh_power = float(((power + power.shift().fillna(power)) / 2 * hours).sum())
        soc = pd.to_numeric(g["batteryChargeLevelPercentage"], errors="coerce")
        types = g["chargingType"].dropna() if "chargingType" in g else pd.Series(dtype=str)
        rows.append({
            "start": g["time"].iloc[0],
            "end": g["time"].iloc[-1],
            "minutes": (g["time"].iloc[-1] - g["time"].iloc[0]).total_seconds() / 60,
            "soc_start": soc.iloc[0],
            "soc_end": soc.iloc[-1],
            "kwh_from_soc": (soc.iloc[-1] - soc.iloc[0]) / 100 * BATTERY_KWH,
            "kwh_from_power": kwh_power,
            "peak_kw": power.max(),
            "type": types.mode().iloc[0].removeprefix("CHARGING_TYPE_") if not types.empty else None,
        })
    return pd.DataFrame(rows, columns=cols)


def derive_all(path):
    db = sqlite3.connect(path)
    b, o = load(db, "battery"), load(db, "odometer")
    return trips(o, b), charge_sessions(b)


def idle_drain(battery, trip_table):
    """Daily SoC lost while parked: drops between consecutive non-charging readings
    that do not overlap a trip. Returns columns date, soc_lost_pct."""
    if battery.empty or "batteryChargeLevelPercentage" not in battery:
        return pd.DataFrame(columns=["date", "soc_lost_pct"])
    b = battery.dropna(subset=["batteryChargeLevelPercentage"]).sort_values("time").reset_index(drop=True)
    charging = b["chargingStatusV2"].isin(CHARGING_STATES) if "chargingStatusV2" in b else pd.Series(False, index=b.index)
    prev = b.shift()
    drop = prev["batteryChargeLevelPercentage"] - b["batteryChargeLevelPercentage"]
    ok = (drop > 0) & ~charging & ~charging.shift(fill_value=False)
    for _, tr in trip_table.iterrows():
        ok &= ~((prev["time"] < tr["end"]) & (b["time"] > tr["start"]))
    out = pd.DataFrame({"date": b.loc[ok, "time"].dt.date, "soc_lost_pct": drop[ok]})
    return out.groupby("date", as_index=False)["soc_lost_pct"].sum()
