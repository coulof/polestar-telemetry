"""Streamlit dashboard over data/telemetry.db. Run: mise run app"""
import json
import os
from contextlib import closing
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from polestar import charging, config, derive, insights, report, theme
from polestar.client import DOMAINS
from polestar.collect import DB_PATH, connect
from polestar.derive import CHARGING_STATES
from polestar.views import bar_chart, charging_notes, chips, empty, fmt_daily, line_chart, location_map, rgba, soc_gauge, state_timeline

st.set_page_config(page_title="Polestar telemetry", page_icon="⚡", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown(theme.CSS, unsafe_allow_html=True)

LOCAL_TZ = ZoneInfo(config.load()["timezone"])   # not the machine's: the server container runs in UTC


def battery_kwh():
    return st.session_state.get("battery_kwh", derive.BATTERY_KWH)


@st.cache_data(ttl=60)
def load_all(path):
    with closing(connect(path)) as db:
        data = {d: derive.load(db, d) for d in DOMAINS}
        counts = dict(db.execute("SELECT domain, COUNT(*) FROM snapshots GROUP BY 1").fetchall())
    for df in data.values():
        if not df.empty:
            df["time"] = df["time"].dt.tz_convert(LOCAL_TZ)
    return data, counts


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
    avail = data["availability"]
    driving = (not avail.empty) and avail.iloc[-1].get("usageMode") in insights.DRIVING_MODES

    with st.container(border=True, key="hero"):
        status = str(b.get("chargingStatusV2", "")).removeprefix("CHARGING_STATUS_V2_").replace("_", " ").capitalize()
        if driving:                 # what the car is doing beats its (idle) charge state
            st.markdown(theme.chip("Driving", True, "gold"), unsafe_allow_html=True)
        else:
            st.markdown(theme.chip("Parked" if status in ("Idle", "") else status, charging), unsafe_allow_html=True)
        left, right = st.columns([1, 2], vertical_alignment="center")
        soc = b.get("batteryChargeLevelPercentage")
        if pd.notna(soc):
            left.plotly_chart(soc_gauge(soc), width="stretch", key="soc_gauge", config={"displayModeBar": False})
        with right:
            rng = b.get("estimatedDistanceToEmptyKm")
            if pd.notna(rng):
                st.markdown(f'<div class="pt-sub">Estimated range</div><div class="pt-big" style="margin-bottom:.6rem">{rng:.0f} km</div>',
                            unsafe_allow_html=True)
            if charging:
                sessions = derive.price_sessions(derive.charge_sessions(battery, battery_kwh()), location, cfg, battery)
                cur = sessions.iloc[-1] if not sessions.empty else None
                c = st.columns(3)
                c[0].metric("Power", f"{(b.get('chargingPowerWatts') or 0) / 1000:.1f} kW",
                            str(b.get("chargingType", "")).removeprefix("CHARGING_TYPE_") or None,
                            delta_color="off", delta_arrow="off")
                part = cur is not None and bool(cur["partial"])   # began before tracking: lower bound
                note = ("Energy since the first reading we stored. Charging began before tracking started, "
                        "so the real total is higher.") if part else None
                c[1].metric("Added", f"{cur['kwh_from_power']:.1f} kWh" if cur is not None else "—",
                            "since tracking began" if part else None, delta_color="off", delta_arrow="off", help=note)
                cost = f"€{cur['cost']:.2f}" if cur is not None and pd.notna(cur["cost"]) else "—"
                place = cur["location"] if cur is not None and isinstance(cur["location"], str) else None
                c[2].metric("Cost", cost, place, delta_color="off", delta_arrow="off", help=note)
            if charging or b.get("chargerConnectionStatus") == "CHARGER_CONNECTION_STATUS_CONNECTED":
                with closing(connect(DB_PATH)) as ndb:      # this fragment reruns every minute: do not leak connections
                    notes = charging_notes(ndb, b, charging, now, cfg, location)
                for line_note in notes:
                    st.markdown(line_note)
        age = int((now - b["time"]).total_seconds() // 60)
        st.caption(f"Last battery event {b['time']:%d %b %H:%M} ({age} min ago)"
                   + (f" · driving since {avail.iloc[-1]['time']:%H:%M}" if driving else ""))

    line_md, warn = chips(data["exterior"], data["health"], data["preCleaning"], cfg, driving)
    if line_md:
        st.markdown(line_md)
    if warn:
        st.warning(warn, icon=":material/build:")

    if not location.empty and "coordinate.latitude" in location:
        with st.container(border=True):
            st.plotly_chart(location_map(location.iloc[-1], cfg), width="stretch", key="car_map")
            seen = location["time"].iloc[-1]
            mins = int((now - seen).total_seconds() // 60)
            ago = f"{mins // 60} h {mins % 60:02d} min ago" if mins >= 60 else f"{mins} min ago"
            st.caption(f"Last position {seen:%d %b %H:%M} ({ago})")
            if driving and seen < avail.iloc[-1]["time"]:
                st.caption(":material/info: No position reported since the drive began; the pin is where the car was last seen.")


# ---- sidebar -------------------------------------------------------------
with st.sidebar:
    st.header("Settings")
    st.number_input("Usable battery (kWh)", 40.0, 120.0, derive.BATTERY_KWH, 1.0, key="battery_kwh",
                    help="Used to turn SoC changes into kWh. Estimate.")   # per session: not a shared module global
    period_days = st.selectbox("Period", [7, 30, 90, 365, 0], index=1,
                               format_func=lambda d: "All" if d == 0 else f"Last {d} days")
    if st.button("Reload data"):
        st.cache_data.clear()

st.title("Polestar 2")

if not os.path.exists(DB_PATH):
    st.error(f"No database at {DB_PATH}. Run `mise run collect` first.")
    st.stop()

data, counts = load_all(DB_PATH)
battery, odometer, location = data["battery"], data["odometer"], data["location"]

now_local = pd.Timestamp.now(tz=LOCAL_TZ)
xrange = None   # x-axis span of time charts: the chosen period, so the axis changes even when data is sparse
if period_days:
    cutoff = now_local - pd.Timedelta(days=period_days)
    xrange = [cutoff, now_local]
    battery_v = battery[battery["time"] >= cutoff] if not battery.empty else battery
else:
    battery_v = battery

trip_table = derive.trips(odometer, battery, availability=data["availability"], battery_kwh=battery_kwh())
sessions = derive.charge_sessions(battery, battery_kwh())
if period_days and not trip_table.empty:
    trip_table = trip_table[trip_table["start"] >= cutoff]
if period_days and not sessions.empty:
    sessions = sessions[sessions["start"] >= cutoff]
sessions = derive.price_sessions(sessions, location, config.load(), battery)

MOCK = os.environ.get("POLESTAR_MOCK") == "1"   # design mock-up on synthetic data, never on the server
tabs = st.tabs(["Now", "Report", "Trips", "Charging", "Battery", "Insights", "Air", "Data"]
               + (["Compare (mock)", "Path (mock)"] if MOCK else []))
tab_now, tab_report, tab_trips, tab_charge, tab_batt, tab_ins, tab_air, tab_data = tabs[:8]

# ---- now -----------------------------------------------------------------
with tab_now:
    now_tab()

# ---- weekly report -------------------------------------------------------
with tab_report:
    rdb = connect(DB_PATH)
    weeks = report.list_weeks(rdb)
    if not weeks:
        empty("No weekly report yet. A timer on the server writes one every Monday morning; "
              "run `deploy/weekly-report.sh` there (or `mise run report` locally) to create the first.",
              ":material/auto_awesome:")
    else:
        wk = st.selectbox("Week", [w["week"] for w in weeks], label_visibility="collapsed")
        r = report.get(rdb, wk)
        week_stats = json.loads(r["stats"])
        with st.container(border=True):
            st.caption(f"{week_stats['period']['start']} to {week_stats['period']['end']}")
            st.markdown(r["markdown"])
        st.caption(f"Written by Claude ({r['model']}) on {r['created_at'][:10]} from estimated figures; "
                   "only aggregates were sent, no VIN or coordinates.")
        with st.expander("Numbers behind it"):
            st.json(week_stats, expanded=1)

rdb.close()

# ---- trips ---------------------------------------------------------------
with tab_trips:
    if trip_table.empty:
        empty("No trips yet. A trip appears once the odometer rises between readings; "
              "keep the collector running while you drive.", ":material/route:")
    else:
        c = st.columns(3)
        c[0].metric("Trips", len(trip_table), border=True)
        c[1].metric("Distance", f"{trip_table['km'].sum():.0f} km", border=True)
        kwh100 = derive.avg_consumption(trip_table)
        c[2].metric("Avg consumption (est.)", f"{kwh100:.1f} kWh/100 km" if pd.notna(kwh100) else "—", border=True,
                    help="From the SoC drop over trips with a battery reading at both ends." )
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
    has_saving_tip = (not sessions.empty) and float(sessions['saving'].sum()) > 0.005
    if sessions.empty:
        empty("No charging sessions yet.", ":material/ev_station:")
    else:
        cdb = connect(DB_PATH)
        amp = charging.amp_limit(charging.latest_payload(cdb, "ampLimit"))
        cdb.close()
        c = st.columns(4)
        c[0].metric("Energy", f"{sessions['kwh_from_power'].sum():.0f} kWh", border=True,
                    help=f"{len(sessions)} session(s) in this period.")
        c[1].metric("Peak power", f"{sessions['peak_kw'].max():.1f} kW", border=True)
        priced = sessions["cost"].notna()
        c[2].metric("Cost", f"€{sessions['cost'].sum():.2f}", border=True,
                    help=f"{int(priced.sum())} of {len(sessions)} sessions at a priced location (config.toml)")
        has_tariff = any(l.get("tariff") for l in config.load()["locations"])
        c[3].metric("Could have saved", f"€{sessions['saving'].sum():.2f}" if has_tariff else "—", border=True,
                    help="Energy charged at peak rates, priced at the cheapest rate instead. Needs time-of-use "
                         "tariffs in config.toml; it assumes the charging could all move to the cheap window.")
        capped = sum(charging.amp_check(amp, m) is not None for m in sessions["median_amps"].dropna())
        if capped:
            st.warning(f"{capped} of {len(sessions)} sessions ran below the {amp:.0f} A limit set in the car; "
                       "the wall box, cable or grid may be capping the current.", icon=":material/speed:")
        pick = st.selectbox("Session", sessions.index[::-1],
                            format_func=lambda i: f"{sessions.loc[i, 'start']:%d %b %H:%M} · {sessions.loc[i, 'type'] or '?'}")
        s = sessions.loc[pick]
        cw = battery[(battery["time"] >= s["start"]) & (battery["time"] <= s["end"])].copy()
        cw["kw"] = pd.to_numeric(cw.get("chargingPowerWatts"), errors="coerce") / 1000
        st.plotly_chart(line_chart(cw, "time", "kw", "Charging power", "kW", mode="lines+markers", step=True),
                        width="stretch")
        place = s["location"] if isinstance(s["location"], str) else "unpriced location"
        cost = ((f"€{s['cost']:.2f} at €{s['price_per_kwh']:.2f}/kWh" + (f" + €{s['fee']:.2f} session fee" if s["fee"] else ""))
                if pd.notna(s["cost"]) else "cost unknown")
        if s["partial"]:
            st.caption(":material/info: This session began before tracking started, so energy and SoC change are lower bounds.")
        if pd.notna(s["saving"]) and s["saving"] > 0.005:
            st.info(f"Part of this session was at peak rates: the same energy at the cheapest rate would have cost "
                    f"€{s['saving']:.2f} less (€{s['cost']:.2f} instead of €{s['cost_cheapest']:.2f}).",
                    icon=":material/savings:")
        st.caption(f"SoC {s['soc_start']:.0f} → {s['soc_end']:.0f} %, "
                   f"{s['kwh_from_power']:.1f} kWh from power / {s['kwh_from_soc']:.1f} kWh from SoC. "
                   f"{place}: {cost}.")
        with st.expander("Table"):
            st.dataframe(sessions.round({"minutes": 1, "kwh_from_soc": 1, "kwh_from_power": 1, "peak_kw": 1,
                                         "price_per_kwh": 2, "fee": 2, "cost": 2, "cost_cheapest": 2, "saving": 2,
                                         "median_amps": 0}), hide_index=True, width="stretch")

    with st.expander("Car charging settings"):
        cdb = connect(DB_PATH)
        gt_on = ((charging.latest_payload(cdb, "globalChargeTimer") or {}).get("globalChargeTimer") or {}).get("activated")
        tp, ap = charging.latest_payload(cdb, "targetSoc"), charging.latest_payload(cdb, "ampLimit")
        gt = (charging.latest_payload(cdb, "globalChargeTimer") or {}).get("globalChargeTimer") or {}
        pct = (charging.latest_payload(cdb, "parkingClimateTimer") or {}).get("parkingClimateTimers") or []
        rows = [("Target charge", f"{charging.target_soc(tp):.0f} %" if charging.target_soc(tp) is not None else "—"),
                ("Amp limit", f"{charging.amp_limit(ap):.0f} A" if charging.amp_limit(ap) is not None else "—"),
                ("Charge timer", (f"{fmt_daily(gt.get('start'))} to {fmt_daily(gt.get('stop'))}, "
                                  + ("on" if gt.get("activated") else "off")) if gt else "—")]
        for t in pct:
            days = ", ".join(d[:3].title() for d in t.get("weekdays") or []) or "one-off"
            rows.append((f"Ready-by timer {int(t.get('index', 0)) + 1}",
                         f"{fmt_daily(t.get('readyAt'))} ({days}), {'on' if t.get('activated') else 'off'}"))
        cdb.close()
        st.dataframe(pd.DataFrame(rows, columns=["Setting", "Value"]), hide_index=True, width="stretch")
        if has_saving_tip and not gt_on:
            st.caption(":material/lightbulb: The car's charge timer is off, so charging starts as soon as you plug in. "
                       "Turning it on would move charging into the cheap window.")
        st.caption("Read from the car; this app never changes settings. Charge locations are not provided for this car.")

# ---- battery -------------------------------------------------------------
with tab_batt:
    if battery_v.empty:
        empty("No battery data in this period.", ":material/battery_unknown:")
    else:
        fig = line_chart(battery_v.dropna(subset=["batteryChargeLevelPercentage"]), "time",
                         "batteryChargeLevelPercentage", "State of charge", "%", step=True, fill=True)
        fig.update_yaxes(range=[0, 100])
        if xrange:
            fig.update_xaxes(range=xrange)
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

# ---- insights ------------------------------------------------------------
with tab_ins:
    cfg_i = config.load()
    rr = insights.real_range(trip_table)
    cap = insights.capacity_trend(sessions)
    month = insights.monthly(sessions, trip_table, cfg_i["co2_g_per_kwh"], LOCAL_TZ)
    this = month.iloc[-1] if not month.empty else None
    c = st.columns(4)
    c[0].metric("Real-world range", f"{rr['range_100_km']:.0f} km" if rr else "—", border=True,
                help="Km driven per % of SoC on your trips, extrapolated to 100 %. Needs trips with a SoC drop of 2 % or more.")
    c[1].metric("Energy per full charge", f"{cap['capacity_kwh'].median():.0f} kWh" if len(cap) else "—", border=True,
                help="Median over charge sessions that added 10 % or more, measured at the charger (includes losses, "
                     "so above the usable battery size).")
    cost100 = this["cost_per_100km"] if this is not None else float("nan")
    c[2].metric("Cost per 100 km", f"€{cost100:.2f}" if pd.notna(cost100) else "—", border=True,
                help="Latest month in the period: charging cost divided by km driven. Needs priced sessions and trips.")
    if cfg_i["co2_g_per_kwh"] is None:
        c[3].metric("CO2", "—", border=True, help="Set [co2] g_per_kwh in config.toml to enable.")
    else:
        g = this["co2_g_per_km"] if this is not None else float("nan")
        c[3].metric("CO2", f"{g:.0f} g/km" if pd.notna(g) else "—", border=True,
                    help=f"Charged energy x {cfg_i['co2_g_per_kwh']} g/kWh, per km driven (latest month).")
    if len(cap):
        st.plotly_chart(line_chart(cap, "start", "capacity_kwh", "Energy per full charge, by session", "kWh",
                                   mode="lines+markers"), width="stretch")
    else:
        empty("Capacity estimates need a charge session that adds at least 10 % of SoC.", ":material/battery_charging_full:")
    window = now_local - pd.Timedelta(days=min(period_days or 14, 14))
    seg = insights.state_segments(data["availability"], derive.charge_sessions(battery, battery_kwh()), pd.Timestamp.now(tz=LOCAL_TZ))
    if not seg.empty:
        seg = seg[seg["end"] >= window].assign(start=lambda d: d["start"].clip(lower=window))
    if seg.empty:
        empty("No state changes in the last 14 days.", ":material/timeline:")
    else:
        st.plotly_chart(state_timeline(seg), width="stretch")
        st.caption("Parked and driving come from the car's usage-mode events; they update only when the mode changes.")
    if not month.empty:
        st.subheader("By month")
        st.dataframe(month.round({"km": 0, "kwh": 1, "cost": 2, "cost_per_100km": 2, "co2_kg": 1, "co2_g_per_km": 0}),
                     hide_index=True, width="stretch")

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
            for col, title, unit in (("aqi", "Air quality index", "index"), ("pm25", "PM2.5", "")):
                fig = line_chart(air_v.dropna(subset=[col]), "time", col, title, unit, mode="lines+markers", step=True)
                if xrange:
                    fig.update_xaxes(range=xrange)
                st.plotly_chart(fig, width="stretch")
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
        with closing(connect(DB_PATH)) as ddb:
            raw = json.loads(ddb.execute(
                "SELECT payload FROM snapshots WHERE domain=? ORDER BY ts DESC LIMIT 1", (domain,)).fetchone()[0])
        raw.pop("vin", None)
        st.json(raw)

if MOCK:
    from polestar import mock_compare, mock_path
    with tabs[8]:
        mock_compare.render()
    with tabs[9]:
        mock_path.render()
