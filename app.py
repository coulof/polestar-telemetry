"""Streamlit dashboard over data/telemetry.db. Run: mise run app"""
import json
import os
import re
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from polestar import config, derive
from polestar.client import DOMAINS
from polestar.collect import DB_PATH, connect

st.set_page_config(page_title="Polestar telemetry", page_icon="⚡", layout="wide")

LOCAL_TZ = datetime.now().astimezone().tzinfo


def series_color():
    try:
        dark = st.context.theme.type == "dark"
    except Exception:
        dark = False
    return "#3987e5" if dark else "#2a78d6"


@st.cache_data(ttl=60)
def load_all(path):
    db = connect(path)
    data = {d: derive.load(db, d) for d in DOMAINS}
    for df in data.values():
        if not df.empty:
            df["time"] = df["time"].dt.tz_convert(LOCAL_TZ)
    counts = dict(db.execute("SELECT domain, COUNT(*) FROM snapshots GROUP BY 1").fetchall())
    return data, counts


def line(df, x, y, title, ytitle, mode="lines", step=False, height=300):
    fig = go.Figure(go.Scatter(
        x=df[x], y=df[y], mode=mode, line=dict(color=series_color(), width=2, shape="hv" if step else "linear"),
        marker=dict(size=8, color=series_color(), line=dict(width=2, color="rgba(0,0,0,0)")),
        hovertemplate="%{x|%d %b %H:%M}<br>%{y:.1f} " + ytitle + "<extra></extra>",
    ))
    fig.update_layout(title=title, yaxis_title=ytitle, height=height, margin=dict(l=0, r=0, t=40, b=0),
                      hovermode="x unified", showlegend=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.2)", zeroline=False)
    fig.update_xaxes(showgrid=False)
    return fig


def bars(df, x, y, title, ytitle, height=300):
    fig = go.Figure(go.Bar(x=df[x], y=df[y], marker_color=series_color(),
                           hovertemplate="%{x}<br>%{y:.1f} " + ytitle + "<extra></extra>"))
    fig.update_layout(title=title, yaxis_title=ytitle, height=height, margin=dict(l=0, r=0, t=40, b=0),
                      showlegend=False, bargap=0.4)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.2)", zeroline=False)
    return fig


def empty(msg):
    st.info(msg)


# ---- sidebar -------------------------------------------------------------
with st.sidebar:
    st.header("Settings")
    derive.BATTERY_KWH = st.number_input("Usable battery (kWh)", 40.0, 120.0, derive.BATTERY_KWH, 1.0,
                                         help="Used to turn SoC changes into kWh. Estimate.")
    days = st.selectbox("Period", [7, 30, 90, 365, 0], index=1, format_func=lambda d: "All" if d == 0 else f"Last {d} days")
    if st.button("Reload data"):
        st.cache_data.clear()

if not os.path.exists(DB_PATH):
    st.error(f"No database at {DB_PATH}. Run `mise run collect` first.")
    st.stop()

data, counts = load_all(DB_PATH)
battery, odometer, location, availability = (data[d] for d in DOMAINS[:4])
exterior, health = data["exterior"], data["health"]

if days:
    cutoff = pd.Timestamp.now(tz=LOCAL_TZ) - pd.Timedelta(days=days)
    battery_v = battery[battery["time"] >= cutoff] if not battery.empty else battery
else:
    battery_v = battery

trip_table = derive.trips(odometer, battery)
sessions = derive.charge_sessions(battery)
if days and not trip_table.empty:
    trip_table = trip_table[trip_table["start"] >= cutoff]
if days and not sessions.empty:
    sessions = sessions[sessions["start"] >= cutoff]
sessions = derive.price_sessions(sessions, location, config.load())

st.title("Polestar 2 telemetry")
tab_now, tab_trips, tab_charge, tab_batt, tab_data = st.tabs(["Now", "Trips", "Charging", "Battery", "Data"])

# ---- now -----------------------------------------------------------------
with tab_now:
    if battery.empty:
        empty("No battery data yet.")
    else:
        b = battery.iloc[-1]
        age = pd.Timestamp.now(tz=LOCAL_TZ) - b["time"]
        c = st.columns(4)
        c[0].metric("Charge", f"{b.get('batteryChargeLevelPercentage', float('nan')):.0f} %")
        c[1].metric("Range", f"{b.get('estimatedDistanceToEmptyKm', float('nan')):.0f} km")
        status = str(b.get("chargingStatusV2", "")).removeprefix("CHARGING_STATUS_V2_").replace("_", " ").title()
        c[2].metric("Status", status or "—")
        kw = (b.get("chargingPowerWatts") or 0) / 1000
        c[3].metric("Charging power", f"{kw:.1f} kW")
        st.caption(f"Last battery event {b['time']:%d %b %H:%M} ({int(age.total_seconds() // 60)} min ago)")
    if not exterior.empty:
        e = exterior.iloc[-1]
        open_parts = [re.sub(r"(?<=[a-z])(?=[A-Z])", " ", c).lower()
                      for c in e.index if str(e[c]) == "OPEN_STATUS_OPEN"]
        lock = (":material/lock: Locked" if e.get("centralLock") == "LOCK_STATUS_LOCKED"
                else ":red[:material/lock_open: Unlocked]")
        closed = (":material/check_circle: All closed" if not open_parts
                  else f":red[:material/warning: Open: {', '.join(open_parts)}]")
        st.markdown(f"{lock} &nbsp;·&nbsp; {closed}")
    if not health.empty:
        h = health.iloc[-1]
        c = st.columns(2)
        c[0].metric("Next service in", f"{h.get('daysToService', float('nan')):.0f} days")
        c[1].metric("…or", f"{h.get('distanceToServiceKm', float('nan')):,.0f} km")
    if not location.empty and "coordinate.latitude" in location:
        loc = location.iloc[-1]
        st.map(pd.DataFrame({"lat": [loc["coordinate.latitude"]], "lon": [loc["coordinate.longitude"]]}), zoom=13)
        st.caption(f"Last position {loc['time']:%d %b %H:%M}")

# ---- trips ---------------------------------------------------------------
with tab_trips:
    if trip_table.empty:
        empty("No trips yet. A trip appears once the odometer rises between readings; "
              "keep `mise run watch` running while you drive.")
    else:
        c = st.columns(3)
        c[0].metric("Trips", len(trip_table))
        c[1].metric("Distance", f"{trip_table['km'].sum():.0f} km")
        kwh100 = trip_table["kwh"].sum() / trip_table["km"].sum() * 100 if trip_table["kwh"].notna().any() else float("nan")
        c[2].metric("Avg consumption (est.)", f"{kwh100:.1f} kWh/100 km")
        daily = trip_table.assign(day=trip_table["start"].dt.date).groupby("day", as_index=False)["km"].sum()
        st.plotly_chart(bars(daily, "day", "km", "Distance per day", "km"), width="stretch")
        eff = trip_table.dropna(subset=["kwh_per_100km"])
        if len(eff):
            st.plotly_chart(line(eff, "start", "kwh_per_100km", "Consumption per trip (estimated from SoC)",
                                 "kWh/100 km", mode="lines+markers"), width="stretch")
        with st.expander("Table"):
            st.dataframe(trip_table.round({"km": 1, "minutes": 0, "avg_kmh": 0, "kwh": 1, "kwh_per_100km": 1}), hide_index=True, width="stretch")

# ---- charging ------------------------------------------------------------
with tab_charge:
    if sessions.empty:
        empty("No charging sessions yet.")
    else:
        c = st.columns(4)
        c[0].metric("Sessions", len(sessions))
        c[1].metric("Energy (from power)", f"{sessions['kwh_from_power'].sum():.0f} kWh")
        c[2].metric("Peak power", f"{sessions['peak_kw'].max():.1f} kW")
        priced = sessions["cost"].notna()
        c[3].metric("Cost", f"€{sessions['cost'].sum():.2f}",
                    help=f"{int(priced.sum())} of {len(sessions)} sessions at a priced location (config.toml)")
        pick = st.selectbox("Session", sessions.index[::-1],
                            format_func=lambda i: f"{sessions.loc[i, 'start']:%d %b %H:%M} · {sessions.loc[i, 'type'] or '?'}")
        s = sessions.loc[pick]
        cw = battery[(battery["time"] >= s["start"]) & (battery["time"] <= s["end"])].copy()
        cw["kw"] = pd.to_numeric(cw.get("chargingPowerWatts"), errors="coerce") / 1000
        st.plotly_chart(line(cw, "time", "kw", "Charging power", "kW", mode="lines+markers", step=True),
                        width="stretch")
        st.caption(f"SoC {s['soc_start']:.0f} → {s['soc_end']:.0f} %, "
                   f"{s['kwh_from_power']:.1f} kWh from power / {s['kwh_from_soc']:.1f} kWh from SoC.")
        place = s["location"] if isinstance(s["location"], str) else "unpriced location"
        cost = f"€{s['cost']:.2f} at €{s['price_per_kwh']:.2f}/kWh" if pd.notna(s["cost"]) else "cost unknown"
        st.caption(f"{place}: {cost}")
        with st.expander("Table"):
            st.dataframe(sessions.round({"minutes": 1, "kwh_from_soc": 1, "kwh_from_power": 1, "peak_kw": 1, "price_per_kwh": 2, "cost": 2}), hide_index=True, width="stretch")

# ---- battery -------------------------------------------------------------
with tab_batt:
    if battery_v.empty:
        empty("No battery data in this period.")
    else:
        st.plotly_chart(line(battery_v.dropna(subset=["batteryChargeLevelPercentage"]), "time",
                             "batteryChargeLevelPercentage", "State of charge", "%", step=True),
                        width="stretch")
        drain = derive.idle_drain(battery_v, trip_table)
        if drain.empty:
            empty("No parked drain measured yet (needs consecutive readings while parked and not charging).")
        else:
            st.plotly_chart(bars(drain, "date", "soc_lost_pct", "SoC lost while parked", "% per day"),
                            width="stretch")
            st.caption("Lower bound: readings are event-driven, so slow drain between events is attributed "
                       "to the day of the next reading.")

# ---- data ----------------------------------------------------------------
with tab_data:
    rows = []
    for d in DOMAINS:
        df = data[d]
        last = df["time"].iloc[-1] if not df.empty else None
        rows.append({"domain": d, "snapshots": counts.get(d, 0), "last event": last})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("Events are pushed only when a value changes, so quiet domains can be hours old.")
    domain = st.selectbox("Latest raw payload", DOMAINS)
    if not data[domain].empty:
        raw = json.loads(connect(DB_PATH).execute(
            "SELECT payload FROM snapshots WHERE domain=? ORDER BY ts DESC LIMIT 1", (domain,)).fetchone()[0])
        raw.pop("vin", None)
        st.json(raw)
