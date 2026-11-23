"""Derive trips and charge sessions from stored snapshots.

Snapshots are sparse and event-driven, so everything here is an estimate:
energy comes from SoC deltas x battery capacity (BATTERY_KWH) or from
integrating reported charging power.
"""
import json
import os
import sqlite3

import pandas as pd

from . import config
from .insights import DRIVING_MODES

BATTERY_KWH = float(os.environ.get("BATTERY_KWH", 78))  # usable; unverified for this variant
MIN_KM_FOR_ENERGY = 3      # SoC moves in 1 % steps (~0.8 kWh): shorter trips get no energy estimate
TRIP_GAP_S = 15 * 60       # fallback (no usage-mode data): odometer readings further apart than this start a new trip
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


def _asof(df, times, cols, direction="backward", tolerance="1h"):
    """Nearest value of `cols` at/before (or at/after) each time, within `tolerance` (None = any)."""
    if df.empty:
        return pd.DataFrame({c: [float("nan")] * len(times) for c in cols})
    # normalise both sides to UTC/ns: callers may hold local-tz or different-resolution times
    utc_ns = "datetime64[ns, UTC]"
    left = pd.DataFrame({"time": pd.to_datetime(times, utc=True).astype(utc_ns)})
    right = df[["time", *cols]].assign(time=df["time"].dt.tz_convert("UTC").astype(utc_ns))
    out = pd.merge_asof(
        left, right, on="time", direction=direction,
        tolerance=pd.Timedelta(tolerance) if tolerance else None,
    )
    return out[cols]


def _driving_windows(availability):
    """(start, end) of each completed drive from `usageMode` events; each window is its own trip.
    A drive still in progress has no end yet and is left out."""
    a = availability.dropna(subset=["usageMode"]).sort_values("time")
    wins, start = [], None
    for t, mode in zip(a["time"], a["usageMode"]):
        if mode in DRIVING_MODES and start is None:
            start = t
        elif mode not in DRIVING_MODES and start is not None:
            wins.append((start, t))
            start = None
    return wins


def _trips_from_windows(odometer, availability):
    """Trips from driving windows: the car reports the odometer only when a drive starts and ends, so the
    distance is the odometer change across the window. Start value: the last reading before it. End value:
    the latest reading up to 10 minutes after it (the end reading can arrive just before the mode change),
    but never past the start of the next drive."""
    o = odometer.dropna(subset=["odometerMeters"]).sort_values("time")
    wins = _driving_windows(availability)
    rows = []
    for i, (s_, e_) in enumerate(wins):
        limit = e_ + pd.Timedelta(minutes=10)
        if i + 1 < len(wins):
            limit = min(limit, wins[i + 1][0])
        before = o[o["time"] <= s_]
        after = o[(o["time"] > s_) & (o["time"] <= limit)]
        if before.empty or after.empty:
            continue
        km = (after["odometerMeters"].max() - before["odometerMeters"].iloc[-1]) / 1000
        if km > 0:
            rows.append({"start": s_, "end": e_, "km": km})
    return pd.DataFrame(rows, columns=["start", "end", "km"])


def _trips_from_odometer(odometer):
    """Fallback without usage-mode data: a run of increasing odometer readings with no big gaps."""
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
    return pd.DataFrame(rows, columns=["start", "end", "km"])


def avg_consumption(trips):
    """kWh per 100 km over the trips that have an energy estimate (NaN when none do)."""
    valid = trips.dropna(subset=["kwh"]) if len(trips) else trips
    km = valid["km"].sum() if len(valid) else 0.0
    return valid["kwh"].sum() / km * 100 if km > 0 else float("nan")


def trips(odometer, battery, location=None, availability=None, battery_kwh=None):
    """One row per trip. With `availability` (usage-mode events) trips are the driving windows; otherwise
    runs of increasing odometer readings. `battery_kwh` overrides the module default for the energy estimate."""
    cols = ["start", "end", "km", "minutes", "avg_kmh", "soc_start", "soc_end", "kwh", "kwh_per_100km"]
    if odometer.empty or "odometerMeters" not in odometer:
        return pd.DataFrame(columns=cols)
    if availability is not None and not availability.empty and "usageMode" in availability:
        t = _trips_from_windows(odometer, availability)
    else:
        t = _trips_from_odometer(odometer)
    if t.empty:
        return pd.DataFrame(columns=cols)
    t["minutes"] = (t["end"] - t["start"]).dt.total_seconds() / 60
    t["avg_kmh"] = t["km"] / (t["minutes"] / 60)
    b = battery.dropna(subset=["batteryChargeLevelPercentage"]) if not battery.empty else battery
    t["soc_start"] = _asof(b, t["start"], ["batteryChargeLevelPercentage"]).to_numpy().ravel()
    col = ["batteryChargeLevelPercentage"]
    after = pd.Series(_asof(b, t["end"], col, "forward").to_numpy().ravel(), index=t.index)
    before = pd.Series(_asof(b, t["end"], col).to_numpy().ravel(), index=t.index)
    t["soc_end"] = after.fillna(before)          # no reading after the drive yet: use the last one before
    t["kwh"] = (t["soc_start"] - t["soc_end"]) / 100 * (battery_kwh or BATTERY_KWH)
    t["kwh_per_100km"] = t["kwh"] / t["km"] * 100
    short = t["km"] < MIN_KM_FOR_ENERGY
    t.loc[short, ["kwh", "kwh_per_100km"]] = float("nan")
    return t[cols]


def charge_sessions(battery, battery_kwh=None):
    """One row per charging session (consecutive charging readings). `battery_kwh` overrides the default."""
    cols = ["start", "end", "minutes", "soc_start", "soc_end", "kwh_from_soc", "kwh_from_power", "peak_kw", "type",
            "median_amps", "partial"]
    if battery.empty or "chargingStatusV2" not in battery:
        return pd.DataFrame(columns=cols)
    b = battery.sort_values("time").reset_index(drop=True)
    charging = b["chargingStatusV2"].isin(CHARGING_STATES)
    # A session ends at the first non-charging reading, or after a long silence.
    gap = b["time"].diff().dt.total_seconds() > CHARGE_GAP_S
    sid = (charging.ne(charging.shift()) | gap).cumsum()
    rows = []
    for _, g in b[charging].groupby(sid[charging]):
        first = g.index[0]
        # We only know when charging began if a non-charging reading precedes it without a long silence.
        partial = first == 0 or bool(charging.iloc[first - 1]) or bool(gap.iloc[first])
        hours = g["time"].diff().dt.total_seconds().fillna(0) / 3600
        power = pd.to_numeric(g.get("chargingPowerWatts"), errors="coerce").fillna(0) / 1000
        kwh_power = float(((power + power.shift().fillna(power)) / 2 * hours).sum())
        soc = pd.to_numeric(g["batteryChargeLevelPercentage"], errors="coerce")
        types = g["chargingType"].dropna() if "chargingType" in g else pd.Series(dtype=str)
        amps = pd.to_numeric(g["chargingCurrentAmps"], errors="coerce") if "chargingCurrentAmps" in g else pd.Series(dtype=float)
        rows.append({
            "start": g["time"].iloc[0],
            "end": g["time"].iloc[-1],
            "minutes": (g["time"].iloc[-1] - g["time"].iloc[0]).total_seconds() / 60,
            "soc_start": soc.iloc[0],
            "soc_end": soc.iloc[-1],
            "kwh_from_soc": (soc.iloc[-1] - soc.iloc[0]) / 100 * (battery_kwh or BATTERY_KWH),
            "kwh_from_power": kwh_power,
            "peak_kw": power.max(),
            "type": types.mode().iloc[0].removeprefix("CHARGING_TYPE_") if not types.empty else None,
            "median_amps": amps.median() if amps.notna().any() else None,
            "partial": partial,   # started before tracking: energy and SoC change are lower bounds
        })
    return pd.DataFrame(rows, columns=cols)


def derive_all(path):
    db = sqlite3.connect(path)
    b, o, a = load(db, "battery"), load(db, "odometer"), load(db, "availability")
    return trips(o, b, availability=a), charge_sessions(b)


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


def _tou_cost(readings, loc, tz):
    """Cost of one session under time-of-use tariffs: each reading interval's energy (trapezoid of the
    reported power) is split into one-minute steps and priced at the tariff in force at that local time."""
    r = readings.sort_values("time")
    t = r["time"].dt.tz_convert("UTC").tolist()
    kw = (pd.to_numeric(r.get("chargingPowerWatts"), errors="coerce").fillna(0) / 1000).tolist()
    cost = cheapest = 0.0
    for i in range(len(t) - 1):
        secs = (t[i + 1] - t[i]).total_seconds()
        if secs <= 0:
            continue
        energy = (kw[i] + kw[i + 1]) / 2 * secs / 3600
        steps = max(1, int(-(-secs // 60)))
        for k in range(steps):
            mid = (t[i] + pd.Timedelta(seconds=secs * (k + 0.5) / steps)).tz_convert(tz)
            cost += energy / steps * config.price_at(loc, mid)
            cheapest += energy / steps * config.cheapest_price(loc, mid)
    return cost, cheapest


def price_sessions(sessions, location, cfg, battery=None, manual=None, locations=None):
    """Add location, price_per_kwh, fee and cost (EUR) to charge sessions. cost = energy + the location's
    optional `session_fee` (fixed amount per session); price_per_kwh is the energy price only.

    The car is assumed to be where it last reported a position before the session started. A location
    with [[location.tariff]] windows is priced per reading interval (needs `battery`); price_per_kwh is
    then the effective average.

    `manual` ({start epoch seconds: total paid}, see costs.py) replaces the estimate of those sessions:
    cost is the amount entered, there is no saving to show, and `manual` is True.

    `locations` ({start epoch seconds: name}, see costs.py) overrides the detected place: a config.toml location
    name is priced with its tariff and fee, any other name is a label priced at the default price;
    `location_manual` is then True."""
    out = sessions.copy()
    lat_col, lon_col = "coordinate.latitude", "coordinate.longitude"
    if len(out) and not location.empty and lat_col in location:
        pos = _asof(location, out["start"], [lat_col, lon_col], tolerance=None)
        locs = [config.match(cfg, la, lo) for la, lo in zip(pos[lat_col], pos[lon_col])]
    else:
        locs = [None] * len(out)
    set_by_hand = [(locations or {}).get(int(t.timestamp())) for t in out["start"]]
    by_name = {x["name"]: x for x in cfg["locations"]}
    locs = [by_name.get(n) if n else loc for n, loc in zip(set_by_hand, locs)]
    tz = cfg.get("timezone", "Europe/Paris")
    names, prices, costs, cheapest_costs, fees = [], [], [], [], []
    for (_, sess), loc, label in zip(out.iterrows(), locs, set_by_hand):
        name = label or (loc["name"] if loc else None)
        price = loc["price_per_kwh"] if loc else cfg["default_price"]
        cost = sess["kwh_from_power"] * price if price is not None else float("nan")
        cheapest = cost          # no tariff windows: nothing cheaper to compare with
        if loc and loc.get("tariff") and battery is not None and not battery.empty:
            window = battery[(battery["time"] >= sess["start"]) & (battery["time"] <= sess["end"])]
            if len(window) > 1:
                cost, cheapest = _tou_cost(window, loc, tz)
                price = cost / sess["kwh_from_power"] if sess["kwh_from_power"] else price
        fee = float(loc.get("session_fee") or 0) if loc else 0.0    # fixed charge per session at this place
        if fee and pd.notna(cost):
            cost, cheapest = cost + fee, cheapest + fee            # a fee does not change what timing could save
        names.append(name)
        prices.append(price)
        costs.append(cost)
        cheapest_costs.append(cheapest)
        fees.append(fee)
    out["location"] = names
    out["location_manual"] = pd.Series([n is not None for n in set_by_hand], index=out.index, dtype=bool)
    out["fee"] = pd.Series(fees, index=out.index, dtype=float)
    out["price_per_kwh"] = pd.to_numeric(pd.Series(prices, index=out.index), errors="coerce")
    out["cost"] = pd.Series(costs, index=out.index, dtype=float)
    # what the same energy would have cost at the location's cheapest rate (moving it all off-peak)
    out["cost_cheapest"] = pd.Series(cheapest_costs, index=out.index, dtype=float)
    out["saving"] = out["cost"] - out["cost_cheapest"]
    out["manual"] = False
    if manual and len(out):
        entered = out["start"].map(lambda t: manual.get(int(t.timestamp())))
        hit = entered.notna()
        out.loc[hit, "cost"] = entered[hit].astype(float)
        out.loc[hit, "cost_cheapest"] = out.loc[hit, "cost"]
        out.loc[hit, "saving"] = 0.0
        out.loc[hit, "fee"] = 0.0
        out.loc[hit, "price_per_kwh"] = out.loc[hit, "cost"] / out.loc[hit, "kwh_from_power"].where(out["kwh_from_power"] > 0)
        out.loc[hit, "manual"] = True
    return out


def _epoch(df, col):
    """`<col>.seconds` (epoch, flattened by json_normalize) as UTC datetimes; NaT if absent."""
    name = f"{col}.seconds"
    if name not in df:
        return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
    return pd.to_datetime(pd.to_numeric(df[name], errors="coerce"), unit="s", utc=True).astype("datetime64[ns, UTC]")


def air_quality(pre):
    """Cabin air readings from `preCleaning` snapshots: time (UTC), aqi, pm25, last_cycle, cycle_valid.

    The API does not document the index scale or the PM2.5 unit."""
    cols = ["time", "aqi", "pm25", "last_cycle", "cycle_valid"]
    if pre.empty or "measuredAirQualityIndex" not in pre:
        return pd.DataFrame(columns=cols)
    event = pre["time"].dt.tz_convert("UTC").astype("datetime64[ns, UTC]")
    out = pd.DataFrame({
        "time": _epoch(pre, "measurementDate").fillna(event),
        "aqi": pd.to_numeric(pre["measuredAirQualityIndex"], errors="coerce"),
        "pm25": pd.to_numeric(pre.get("measuredParticulateMatter25"), errors="coerce"),
        "last_cycle": _epoch(pre, "lastCycleCompleted"),
        "cycle_valid": pre.get("lastCycleValid"),
    })
    out = out.dropna(subset=["aqi", "pm25"], how="all").drop_duplicates("time")
    return out.sort_values("time").reset_index(drop=True)[cols]


def air_daily(air, tz=None):
    """Per local day: mean/max index, min/mean/max PM2.5, and number of readings."""
    cols = ["date", "aqi_avg", "aqi_max", "pm25_min", "pm25_avg", "pm25_max", "readings"]
    if air.empty:
        return pd.DataFrame(columns=cols)
    day = air["time"].dt.tz_convert(tz).dt.date if tz else air["time"].dt.date
    g = air.assign(date=day).groupby("date")
    out = g.agg(aqi_avg=("aqi", "mean"), aqi_max=("aqi", "max"), pm25_min=("pm25", "min"), pm25_avg=("pm25", "mean"),
                pm25_max=("pm25", "max"), readings=("aqi", "size")).reset_index()
    return out[cols]


def cleaning_cycles(air):
    """Completed pre-cleaning cycles (distinct `lastCycleCompleted` values), newest first."""
    if air.empty or air["last_cycle"].isna().all():
        return pd.DataFrame(columns=["completed", "valid"])
    c = air.dropna(subset=["last_cycle"]).groupby("last_cycle", as_index=False)["cycle_valid"].last()
    return c.rename(columns={"last_cycle": "completed", "cycle_valid": "valid"}).sort_values(
        "completed", ascending=False).reset_index(drop=True)
