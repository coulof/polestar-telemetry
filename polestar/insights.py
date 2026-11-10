"""Higher-level figures built on derive.py output: range, capacity, costs, daily states.

All of these are estimates from sparse, event-driven snapshots."""
import pandas as pd

DRIVING_MODES = {"USAGE_MODE_DRIVING", "USAGE_MODE_ACTIVE", "USAGE_MODE_ENGINE_ON"}


def real_range(trips, min_soc_drop=2):
    """Measured range: km driven per % of SoC, extrapolated to 100 %. None if no usable trips."""
    if trips.empty:
        return None
    t = trips.dropna(subset=["soc_start", "soc_end", "km"])
    t = t[(t["soc_start"] - t["soc_end"]) >= min_soc_drop]
    if t.empty:
        return None
    km_per_pct = t["km"].sum() / (t["soc_start"] - t["soc_end"]).sum()
    return {"km_per_pct": km_per_pct, "range_100_km": km_per_pct * 100, "trips": len(t)}


def capacity_trend(sessions, min_delta=10):
    """Energy drawn per charge session scaled to 100 % SoC (kWh), for sessions that added >= min_delta %.

    Measured at the charger side, so it includes charging losses and overestimates usable capacity."""
    cols = ["start", "soc_start", "soc_end", "kwh_from_power", "capacity_kwh"]
    if sessions.empty:
        return pd.DataFrame(columns=cols)
    s = sessions.copy()
    delta = s["soc_end"] - s["soc_start"]
    s = s[delta >= min_delta]
    s["capacity_kwh"] = s["kwh_from_power"] / ((s["soc_end"] - s["soc_start"]) / 100)
    return s[cols].reset_index(drop=True)


def monthly(sessions, trips, g_per_kwh=None, tz=None):
    """Per month: km, kWh charged, cost, sessions, cost per 100 km (and CO2 if a grid factor is given)."""
    cols = ["month", "km", "kwh", "cost", "sessions", "cost_per_100km"]
    if g_per_kwh is not None:
        cols += ["co2_kg", "co2_g_per_km"]

    def month(times):
        t = times.dt.tz_convert(tz) if tz else times
        return t.dt.strftime("%Y-%m")

    s = pd.DataFrame(columns=["month", "kwh", "cost", "sessions"])
    if not sessions.empty:
        g = sessions.assign(month=month(sessions["start"])).groupby("month")
        s = g.agg(kwh=("kwh_from_power", "sum"), cost=("cost", lambda c: c.sum(min_count=1)),
                  sessions=("start", "size")).reset_index()
    k = pd.DataFrame(columns=["month", "km"])
    if not trips.empty:
        k = trips.assign(month=month(trips["start"])).groupby("month", as_index=False)["km"].sum()
    out = s.merge(k, on="month", how="outer").sort_values("month").reset_index(drop=True)
    if out.empty:
        return pd.DataFrame(columns=cols)
    km = out["km"].where(out["km"] > 0)
    out["cost_per_100km"] = out["cost"] / km * 100
    if g_per_kwh is not None:
        out["co2_kg"] = out["kwh"] * g_per_kwh / 1000
        out["co2_g_per_km"] = out["co2_kg"] * 1000 / km
    return out[cols]


def state_segments(availability, sessions, now):
    """Time spans of Driving / Parked (from `usageMode` events) and Charging (from sessions)."""
    rows = []
    if not availability.empty and "usageMode" in availability:
        a = availability.dropna(subset=["usageMode"]).sort_values("time")
        times, modes = list(a["time"]), list(a["usageMode"])
        for i, (t, m) in enumerate(zip(times, modes)):
            end = times[i + 1] if i + 1 < len(times) else now
            rows.append((t, end, "Driving" if m in DRIVING_MODES else "Parked"))
    for _, s in sessions.iterrows():
        rows.append((s["start"], s["end"], "Charging"))
    df = pd.DataFrame(rows, columns=["start", "end", "state"])
    if df.empty:
        return df
    df = df.sort_values(["state", "start"]).reset_index(drop=True)
    # merge back-to-back segments of the same state
    run = ((df["state"] != df["state"].shift()) | (df["start"] > df["end"].shift())).cumsum()
    return df.groupby(run).agg(start=("start", "min"), end=("end", "max"), state=("state", "first")).reset_index(drop=True)
