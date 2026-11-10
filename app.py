"""Streamlit dashboard over data/telemetry.db. Run: mise run app"""
import json
import os
import re
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from polestar import config, derive, theme
from polestar.client import DOMAINS
from polestar.collect import DB_PATH, connect
from polestar.derive import CHARGING_STATES

st.set_page_config(page_title="Polestar telemetry", page_icon="⚡", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown(theme.CSS, unsafe_allow_html=True)

LOCAL_TZ = datetime.now().astimezone().tzinfo


@st.cache_data(ttl=60)
def load_all(path):
    db = connect(path)
    data = {d: derive.load(db, d) for d in DOMAINS}
    for df in data.values():
        if not df.empty:
            df["time"] = df["time"].dt.tz_convert(LOCAL_TZ)
    counts = dict(db.execute("SELECT domain, COUNT(*) FROM snapshots GROUP BY 1").fetchall())
    return data, counts


def rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def fmt_minutes(m):
    h, mm = divmod(int(m), 60)
    return f"{h} h {mm:02d}" if h else f"{mm} min"


def line_chart(df, x, y, title, ytitle, mode="lines", step=False, fill=False, height=300):
    p = theme.palette()
    fig = go.Figure(go.Scatter(
        x=df[x], y=df[y], mode=mode,
        line=dict(color=p["series"], width=2, shape="hv" if step else "linear"),
        marker=dict(size=8, color=p["series"]),
        fill="tozeroy" if fill else None, fillcolor=rgba(p["series"], 0.12),
        hovertemplate="%{x|%d %b %H:%M}<br>%{y:.1f} " + ytitle + "<extra></extra>",
    ))
    fig.update_layout(yaxis_title=ytitle, hovermode="x unified")
    return theme.style(fig, height, title)


def bar_chart(df, x, y, title, ytitle, height=300):
    p = theme.palette()
    fig = go.Figure(go.Bar(x=df[x], y=df[y], marker=dict(color=p["series"], cornerradius=4),
                           hovertemplate="%{x}<br>%{y:.1f} " + ytitle + "<extra></extra>"))
    fig.update_layout(yaxis_title=ytitle, bargap=0.4)
    return theme.style(fig, height, title)


def soc_gauge(soc):
    """Half-circle SoC gauge; tinted zones below 20 % (low) and above 80 % (high), ticks at the edges."""
    p = theme.palette()
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=soc, number=dict(suffix="%", font=dict(size=44, color=p["ink"])),
        gauge=dict(
            axis=dict(range=[0, 100], tickvals=[0, 20, 80, 100], tickfont=dict(size=12, color=p["muted"]),
                      tickcolor=p["muted"]),
            bar=dict(color=p["series"], thickness=0.3), bgcolor="rgba(0,0,0,0)", borderwidth=0,
            steps=[dict(range=[0, 20], color=rgba(p["low"], 0.28)),
                   dict(range=[20, 80], color=p["grid"]),
                   dict(range=[80, 100], color=rgba(p["high"], 0.28))],
        ),
    ))
    return theme.style(fig, 200).update_layout(margin=dict(l=30, r=36, t=10, b=0))


def empty(msg, icon=":material/hourglass_empty:"):
    st.info(msg, icon=icon)


def chips(exterior, health, pre, cfg):
    """Coloured status badges (lock, doors, service, air) as one markdown line."""
    out = []
    if not exterior.empty:
        e = exterior.iloc[-1]
        out.append(":green-badge[:material/lock: Locked]" if e.get("centralLock") == "LOCK_STATUS_LOCKED"
                   else ":red-badge[:material/lock_open: Unlocked]")
        opened = [re.sub(r"(?<=[a-z])(?=[A-Z])", " ", c).lower() for c in e.index if str(e[c]) == "OPEN_STATUS_OPEN"]
        out.append(":green-badge[:material/check_circle: All closed]" if not opened
                   else f":red-badge[:material/warning: Open: {', '.join(opened)}]")
    warn = None
    if not health.empty:
        h = health.iloc[-1]
        d, km = h.get("daysToService"), h.get("distanceToServiceKm")
        if pd.notna(d) and pd.notna(km):
            state = config.service_status(cfg, d, km)
            color = {"ok": "gray", "soon": "orange", "overdue": "red"}[state]
            out.append(f":{color}-badge[:material/build: Service in {d:,.0f} d / {km:,.0f} km]")
            if state != "ok":
                warn = "Service is overdue" if state == "overdue" else \
                    f"Service due in {d:,.0f} days or {km:,.0f} km, whichever comes first"
    if not pre.empty:
        q = pre.iloc[-1]
        if pd.notna(q.get("measuredAirQualityIndex")):
            pm = q.get("measuredParticulateMatter25")
            extra = f" · PM2.5 {pm:.0f}" if pd.notna(pm) else ""
            out.append(f":blue-badge[:material/air: Air quality {q['measuredAirQualityIndex']:.0f}{extra}]")
    return " ".join(out), warn


def location_map(loc, cfg, height=340):
    p = theme.palette()
    lat, lon = loc["coordinate.latitude"], loc["coordinate.longitude"]
    fig = go.Figure()
    if cfg["locations"]:
        fig.add_trace(go.Scattermap(
            lat=[x["lat"] for x in cfg["locations"]], lon=[x["lon"] for x in cfg["locations"]],
            mode="markers+text", marker=dict(size=12, color=p["muted"]), textposition="top right",
            text=[f"{x['name']} · €{x['price_per_kwh']:.2f}/kWh" for x in cfg["locations"]],
            hoverinfo="text", name="priced locations",
        ))
    fig.add_trace(go.Scattermap(lat=[lat], lon=[lon], mode="markers", marker=dict(size=18, color=p["series"]),
                                hovertemplate="Polestar<extra></extra>", name="car"))
    fig.update_layout(map=dict(style="carto-darkmatter" if theme.is_dark() else "carto-positron",
                               center=dict(lat=lat, lon=lon), zoom=13))
    return theme.style(fig, height).update_layout(margin=dict(l=0, r=0, t=0, b=0))


@st.fragment(run_every=60)
def now_tab():
    data, _ = load_all(DB_PATH)
    battery, location = data["battery"], data["location"]
    cfg = config.load()
    if battery.empty:
        empty("No battery data yet.")
        return
    b = battery.iloc[-1]
    charging = b.get("chargingStatusV2") in CHARGING_STATES
    now = pd.Timestamp.now(tz=LOCAL_TZ)

    with st.container(border=True):
        left, right = st.columns([1, 2], vertical_alignment="center")
        soc = b.get("batteryChargeLevelPercentage")
        if pd.notna(soc):
            left.plotly_chart(soc_gauge(soc), width="stretch", key="soc_gauge")
        with right:
            status = str(b.get("chargingStatusV2", "")).removeprefix("CHARGING_STATUS_V2_").replace("_", " ").capitalize()
            st.badge(status or "Unknown", icon=":material/bolt:" if charging else ":material/battery_std:",
                     color="green" if charging else "gray")
            rng = b.get("estimatedDistanceToEmptyKm")
            if pd.notna(rng):
                st.markdown(f'<div class="pt-big">{rng:.0f} km</div><div class="pt-sub">estimated range</div>',
                            unsafe_allow_html=True)
            if charging:
                sessions = derive.price_sessions(derive.charge_sessions(battery), location, cfg)
                cur = sessions.iloc[-1] if not sessions.empty else None
                c = st.columns(4)
                c[0].metric("Power", f"{(b.get('chargingPowerWatts') or 0) / 1000:.1f} kW",
                            str(b.get("chargingType", "")).removeprefix("CHARGING_TYPE_") or None,
                            delta_color="off", delta_arrow="off")
                eta = b.get("estimatedChargingTimeToFullMinutes")
                c[1].metric("To full", fmt_minutes(eta) if pd.notna(eta) else "—")
                c[2].metric("Added", f"{cur['kwh_from_power']:.1f} kWh" if cur is not None else "—")
                cost = f"€{cur['cost']:.2f}" if cur is not None and pd.notna(cur["cost"]) else "—"
                place = cur["location"] if cur is not None and isinstance(cur["location"], str) else None
                c[3].metric("Cost", cost, place, delta_color="off", delta_arrow="off")
        age = int((now - b["time"]).total_seconds() // 60)
        st.caption(f"Last battery event {b['time']:%d %b %H:%M} ({age} min ago)")

    line_md, warn = chips(data["exterior"], data["health"], data["preCleaning"], cfg)
    if line_md:
        st.markdown(line_md)
    if warn:
        st.warning(warn, icon=":material/build:")

    if not location.empty and "coordinate.latitude" in location:
        with st.container(border=True):
            st.plotly_chart(location_map(location.iloc[-1], cfg), width="stretch", key="car_map")
            st.caption(f"Last position {location['time'].iloc[-1]:%d %b %H:%M}")


# ---- sidebar -------------------------------------------------------------
with st.sidebar:
    st.header("Settings")
    derive.BATTERY_KWH = st.number_input("Usable battery (kWh)", 40.0, 120.0, derive.BATTERY_KWH, 1.0,
                                         help="Used to turn SoC changes into kWh. Estimate.")
    period_days = st.selectbox("Period", [7, 30, 90, 365, 0], index=1,
                               format_func=lambda d: "All" if d == 0 else f"Last {d} days")
    if st.button("Reload data"):
        st.cache_data.clear()

if not os.path.exists(DB_PATH):
    st.error(f"No database at {DB_PATH}. Run `mise run collect` first.")
    st.stop()

data, counts = load_all(DB_PATH)
battery, odometer, location = data["battery"], data["odometer"], data["location"]

if period_days:
    cutoff = pd.Timestamp.now(tz=LOCAL_TZ) - pd.Timedelta(days=period_days)
    battery_v = battery[battery["time"] >= cutoff] if not battery.empty else battery
else:
    battery_v = battery

trip_table = derive.trips(odometer, battery)
sessions = derive.charge_sessions(battery)
if period_days and not trip_table.empty:
    trip_table = trip_table[trip_table["start"] >= cutoff]
if period_days and not sessions.empty:
    sessions = sessions[sessions["start"] >= cutoff]
sessions = derive.price_sessions(sessions, location, config.load())

st.title("Polestar 2")
tab_now, tab_trips, tab_charge, tab_batt, tab_air, tab_data = st.tabs(
    ["Now", "Trips", "Charging", "Battery", "Air", "Data"])

# ---- now -----------------------------------------------------------------
with tab_now:
    now_tab()

# ---- trips ---------------------------------------------------------------
with tab_trips:
    if trip_table.empty:
        empty("No trips yet. A trip appears once the odometer rises between readings; "
              "keep the collector running while you drive.", ":material/route:")
    else:
        c = st.columns(3)
        c[0].metric("Trips", len(trip_table), border=True)
        c[1].metric("Distance", f"{trip_table['km'].sum():.0f} km", border=True)
        kwh100 = trip_table["kwh"].sum() / trip_table["km"].sum() * 100 if trip_table["kwh"].notna().any() else float("nan")
        c[2].metric("Avg consumption (est.)", f"{kwh100:.1f} kWh/100 km", border=True)
        daily = trip_table.assign(day=trip_table["start"].dt.date).groupby("day", as_index=False)["km"].sum()
        st.plotly_chart(bar_chart(daily, "day", "km", "Distance per day", "km"), width="stretch")
        eff = trip_table.dropna(subset=["kwh_per_100km"])
        if len(eff):
            st.plotly_chart(line_chart(eff, "start", "kwh_per_100km", "Consumption per trip (estimated from SoC)",
                                       "kWh/100 km", mode="lines+markers"), width="stretch")
        with st.expander("Table"):
            st.dataframe(trip_table.round({"km": 1, "minutes": 0, "avg_kmh": 0, "kwh": 1, "kwh_per_100km": 1}),
                         hide_index=True, width="stretch")

# ---- charging ------------------------------------------------------------
with tab_charge:
    if sessions.empty:
        empty("No charging sessions yet.", ":material/ev_station:")
    else:
        c = st.columns(4)
        c[0].metric("Sessions", len(sessions), border=True)
        c[1].metric("Energy (from power)", f"{sessions['kwh_from_power'].sum():.0f} kWh", border=True)
        c[2].metric("Peak power", f"{sessions['peak_kw'].max():.1f} kW", border=True)
        priced = sessions["cost"].notna()
        c[3].metric("Cost", f"€{sessions['cost'].sum():.2f}", border=True,
                    help=f"{int(priced.sum())} of {len(sessions)} sessions at a priced location (config.toml)")
        pick = st.selectbox("Session", sessions.index[::-1],
                            format_func=lambda i: f"{sessions.loc[i, 'start']:%d %b %H:%M} · {sessions.loc[i, 'type'] or '?'}")
        s = sessions.loc[pick]
        cw = battery[(battery["time"] >= s["start"]) & (battery["time"] <= s["end"])].copy()
        cw["kw"] = pd.to_numeric(cw.get("chargingPowerWatts"), errors="coerce") / 1000
        st.plotly_chart(line_chart(cw, "time", "kw", "Charging power", "kW", mode="lines+markers", step=True),
                        width="stretch")
        place = s["location"] if isinstance(s["location"], str) else "unpriced location"
        cost = f"€{s['cost']:.2f} at €{s['price_per_kwh']:.2f}/kWh" if pd.notna(s["cost"]) else "cost unknown"
        st.caption(f"SoC {s['soc_start']:.0f} → {s['soc_end']:.0f} %, "
                   f"{s['kwh_from_power']:.1f} kWh from power / {s['kwh_from_soc']:.1f} kWh from SoC. "
                   f"{place}: {cost}.")
        with st.expander("Table"):
            st.dataframe(sessions.round({"minutes": 1, "kwh_from_soc": 1, "kwh_from_power": 1, "peak_kw": 1,
                                         "price_per_kwh": 2, "cost": 2}), hide_index=True, width="stretch")

# ---- battery -------------------------------------------------------------
with tab_batt:
    if battery_v.empty:
        empty("No battery data in this period.", ":material/battery_unknown:")
    else:
        fig = line_chart(battery_v.dropna(subset=["batteryChargeLevelPercentage"]), "time",
                         "batteryChargeLevelPercentage", "State of charge", "%", step=True, fill=True)
        fig.update_yaxes(range=[0, 100])
        p = theme.palette()
        for _, sess in sessions.tail(40).iterrows():  # shade charging sessions
            fig.add_vrect(x0=sess["start"], x1=sess["end"], fillcolor=rgba(p["good"], 0.18), line_width=0, layer="below")
        st.plotly_chart(fig, width="stretch")
        st.caption("Shaded: charging sessions.")
        drain = derive.idle_drain(battery_v, trip_table)
        if drain.empty:
            empty("No parked drain measured yet (needs consecutive readings while parked and not charging).")
        else:
            st.plotly_chart(bar_chart(drain, "date", "soc_lost_pct", "SoC lost while parked", "% per day"),
                            width="stretch")
            st.caption("Lower bound: readings are event-driven, so slow drain between events is attributed "
                       "to the day of the next reading.")

# ---- air quality ---------------------------------------------------------
with tab_air:
    air = derive.air_quality(data["preCleaning"])
    if not air.empty:
        air["time"] = air["time"].dt.tz_convert(LOCAL_TZ)
    air_v = air[air["time"] >= cutoff] if period_days and not air.empty else air
    if air.empty:
        empty("No air quality readings yet. They arrive with the car's pre-cleaning measurements.",
              ":material/air:")
    else:
        last = air.iloc[-1]
        c = st.columns(4)
        c[0].metric("Air quality index", f"{last['aqi']:.0f}" if pd.notna(last["aqi"]) else "—", border=True,
                    help="Cabin air quality index reported by the car. The API does not document the scale.")
        c[1].metric("PM2.5", f"{last['pm25']:.0f}" if pd.notna(last["pm25"]) else "—", border=True,
                    help="Particulate matter reported by the car. The API does not document the unit.")
        if pd.notna(last["last_cycle"]):
            ago = pd.Timestamp.now(tz=LOCAL_TZ) - last["last_cycle"]
            c[2].metric("Last pre-cleaning", f"{ago.days} d ago" if ago.days else f"{int(ago.total_seconds() // 3600)} h ago",
                        border=True, help=f"{last['last_cycle']:%d %b %H:%M}")
            c[3].metric("Cycle valid", "Yes" if last["cycle_valid"] else "No", border=True)
        st.caption(f"Last reading {last['time']:%d %b %H:%M}. {len(air)} reading(s) stored.")
        if len(air_v) < 2:
            empty("Trends appear once a few readings are stored (the car reports after each measurement).",
                  ":material/hourglass_empty:")
        else:
            st.plotly_chart(line_chart(air_v.dropna(subset=["aqi"]), "time", "aqi", "Air quality index", "index",
                                       mode="lines+markers", step=True), width="stretch")
            st.plotly_chart(line_chart(air_v.dropna(subset=["pm25"]), "time", "pm25", "PM2.5", "",
                                       mode="lines+markers", step=True), width="stretch")
        daily = derive.air_daily(air_v, LOCAL_TZ)
        if not daily.empty:
            st.subheader("Daily summary")
            st.dataframe(daily.sort_values("date", ascending=False).round(
                {"aqi_avg": 1, "aqi_max": 0, "pm25_avg": 1, "pm25_max": 0}), hide_index=True, width="stretch")
        cycles = derive.cleaning_cycles(air)
        if not cycles.empty:
            st.subheader("Pre-cleaning cycles")
            cycles["completed"] = cycles["completed"].dt.tz_convert(LOCAL_TZ)
            st.dataframe(cycles, hide_index=True, width="stretch")

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
