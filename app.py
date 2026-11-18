"""Streamlit dashboard over data/telemetry.db. Run: mise run app"""
import json
import os
from contextlib import closing
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from polestar import admin, charging, config, costs, derive, insights, path, place_labels, report, theme, views
from polestar.client import DOMAINS
from polestar.collect import DB_PATH, connect
from polestar.derive import CHARGING_STATES
from polestar.views import activity_grid_chart, bar_chart, charging_notes, chips, empty, fmt_daily, line_chart, location_map, rgba, soc_gauge

st.set_page_config(page_title="Polestar telemetry", page_icon="⚡", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown(theme.CSS, unsafe_allow_html=True)
if os.environ.get("POLESTAR_DEMO") == "1":   # public demo (demo/Dockerfile): link back to the project
    st.markdown(theme.demo_ribbon(os.environ.get("POLESTAR_DEMO_URL", "https://github.com/coulof/polestar-telemetry#quick-start")),
                unsafe_allow_html=True)

# Optional login (DASHBOARD_PASSWORD): nothing below runs, so no data is read, until the password is right.
if admin.configured("viewer") and not admin.unlocked(st.session_state, scope="viewer"):
    _, mid, _ = st.columns([1, 2, 1])
    with mid, st.form("login"):
        st.subheader("Polestar telemetry")
        pw = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", width="stretch"):
            problem = admin.unlock(st.session_state, pw, scope="viewer")
            if problem:
                st.error(problem, icon=":material/lock:")
            else:
                st.rerun()
    st.stop()

LOCAL_TZ = ZoneInfo(config.load()["timezone"])   # not the machine's: the server container runs in UTC


def manual_costs():
    with closing(connect(DB_PATH)) as db:
        return costs.load(db)


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
                sessions = derive.price_sessions(derive.charge_sessions(battery, battery_kwh()), location, cfg, battery,
                                                 manual=manual_costs())
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
    period_days = st.selectbox("Period", [1, 7, 30, 90, 365, 0], index=2,
                               format_func=lambda d: "All" if d == 0 else "Last day" if d == 1 else f"Last {d} days")
    if st.button("Reload data"):
        st.cache_data.clear()
    if admin.configured("viewer") and st.button("Sign out"):
        admin.lock(st.session_state, scope="viewer")
        st.rerun()

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
sessions = derive.price_sessions(sessions, location, config.load(), battery, manual=manual_costs())

MOCK = os.environ.get("POLESTAR_MOCK") == "1"   # design mock-up on synthetic data, never on the server
REPORT_ON = config.load()["report"]["enabled"]   # [report] enabled = false hides the Claude integration
names = ["Now", "Report", "Trips", "Charging", "Battery", "Insights", "Path", "Air", "Data"]
if not REPORT_ON:
    names.remove("Report")
names += ["Compare (mock)"] if MOCK else []
tabs = dict(zip(names, st.tabs(names)))
tab_now, tab_trips, tab_charge, tab_batt, tab_ins, tab_path, tab_air, tab_data = (
    tabs[n] for n in ("Now", "Trips", "Charging", "Battery", "Insights", "Path", "Air", "Data"))

# ---- now -----------------------------------------------------------------
with tab_now:
    now_tab()

# ---- weekly report -------------------------------------------------------
if REPORT_ON:
    with tabs["Report"]:
        rdb = connect(DB_PATH)
        weeks = report.list_weeks(rdb)
        failure = report.latest_failure(rdb)
        if failure:
            st.warning(f"The weekly report for {failure['week']} could not be written: {failure['error']} "
                       f"({failure['failed_at'][:16].replace('T', ' ')} UTC)." + (" The report below is the previous one." if weeks else ""),
                       icon=":material/error:")
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
        st.plotly_chart(bar_chart(daily, "day", "km", "Distance per day", "km", xrange=xrange), width="stretch")
        eff = trip_table.dropna(subset=["kwh_per_100km"])
        if len(eff):
            fig = line_chart(eff, "start", "kwh_per_100km", "Consumption per trip (estimated from SoC)", "kWh/100 km",
                             mode="lines+markers")
            st.plotly_chart(fig.update_xaxes(range=xrange) if xrange else fig, width="stretch")   # lines up with the bars
        with st.expander("Table"):
            views.table(trip_table)

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
        c[3].metric("Missed saving", f"€{sessions['saving'].sum():.2f}" if has_tariff else "—", border=True,
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
        cost = ("cost unknown" if pd.isna(s["cost"]) else f"€{s['cost']:.2f} (entered manually)" if s["manual"]
                else f"€{s['cost']:.2f} at €{s['price_per_kwh']:.2f}/kWh" + (f" + €{s['fee']:.2f} session fee" if s["fee"] else ""))
        if s["partial"]:
            st.caption(":material/info: This session began before tracking started, so energy and SoC change are lower bounds.")
        if pd.notna(s["saving"]) and s["saving"] > 0.005:
            st.info(f"Part of this session was at peak rates: the same energy at the cheapest rate would have cost "
                    f"€{s['saving']:.2f} less (€{s['cost']:.2f} instead of €{s['cost_cheapest']:.2f}).",
                    icon=":material/savings:")
        st.caption(f"SoC {s['soc_start']:.0f} → {s['soc_end']:.0f} %, "
                   f"{s['kwh_from_power']:.1f} kWh from power / {s['kwh_from_soc']:.1f} kWh from SoC. "
                   f"{place}: {cost}.")
        with st.expander("Set the cost manually", expanded=bool(s["manual"])):
            if not admin.configured():
                st.caption(":material/lock: Editing is off. Set `ADMIN_PASSWORD` in `.env` and restart to enable it.")
            elif not admin.unlocked(st.session_state):
                with st.form("admin_unlock", clear_on_submit=True, border=False):
                    pw = st.text_input("Admin password", type="password")
                    if st.form_submit_button("Unlock"):
                        problem = admin.unlock(st.session_state, pw)
                        if problem:
                            st.error(problem)
                        else:
                            st.rerun()
            else:
                sk = costs.key(s["start"])
                c1, c2, c3 = st.columns([2, 1, 1], vertical_alignment="bottom")
                amount = c1.number_input("Total paid (€)", min_value=0.0, step=0.5, format="%.2f", key=f"cost_{sk}",
                                         value=float(s["cost"]) if pd.notna(s["cost"]) else 0.0,
                                         help="What you actually paid for this session, fees included. It replaces the estimate here and in the weekly report.")
                if c2.button("Save", key=f"save_{sk}", width="stretch") and admin.unlocked(st.session_state):
                    with closing(connect(DB_PATH)) as wdb:
                        costs.set_cost(wdb, sk, amount)
                    st.rerun()
                if c3.button("Use estimate", key=f"reset_{sk}", width="stretch", disabled=not s["manual"]) and admin.unlocked(st.session_state):
                    with closing(connect(DB_PATH)) as wdb:
                        costs.clear(wdb, sk)
                    st.session_state.pop(f"cost_{sk}", None)
                    st.rerun()
                if st.button("Lock", key="admin_lock", type="tertiary"):
                    admin.lock(st.session_state)
                    st.rerun()
        with st.expander("Table"):
            views.table(sessions)

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
        span = (xrange[1] - xrange[0]) if xrange else (battery_v["time"].max() - battery_v["time"].min())
        for _, sess in sessions.tail(40).iterrows():  # shade charging sessions, never thinner than 1/150 of the axis
            fig.add_vrect(x0=sess["start"], x1=max(sess["end"], sess["start"] + span / 150), fillcolor=rgba(p["good"], 0.18),
                          line_width=0, layer="below")
        st.plotly_chart(fig, width="stretch")
        st.caption("Shaded green: charging sessions.")
        drain = derive.idle_drain(battery_v, trip_table)
        if drain.empty:
            empty("No parked drain measured yet (needs consecutive readings while parked and not charging).")
        else:
            st.plotly_chart(bar_chart(drain, "date", "soc_lost_pct", "SoC lost while parked", "% per day", xrange=xrange),
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
    c[1].metric("Energy per charge", f"{cap['capacity_kwh'].median():.0f} kWh" if len(cap) else "—", border=True,
                help="Energy for a full 0-100 % charge: median over charge sessions that added 10 % or more, measured at the charger (includes losses, "
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
        fig = line_chart(cap, "start", "capacity_kwh", "Energy per full charge, by session", "kWh", mode="markers")
        fig.update_traces(customdata=cap[["kwh_from_power", "soc_start", "soc_end"]], hovertemplate=(   # how each dot is worked out
            "%{x|%d %b %H:%M}<br><b>%{y:.0f} kWh</b> for a full 0-100 % charge<br>"
            "%{customdata[0]:.1f} kWh charged, SoC %{customdata[1]:.0f} → %{customdata[2]:.0f} %, scaled to 100 %<br>"
            "Measured at the charger: includes charging losses<extra></extra>"), selector=dict(type="scatter"))
        fig.update_layout(hovermode="closest")   # one session per tooltip, not a unified date header
        fig.add_hline(y=battery_kwh(), line=dict(color=theme.palette()["baseline"], width=1, dash="dot"),
                      annotation_text="usable battery", annotation_position="bottom right", annotation_font_color=theme.palette()["muted"])
        st.plotly_chart(fig, width="stretch")   # independent sessions: dots, not a line
    else:
        empty("Capacity estimates need a charge session that adds at least 10 % of SoC.", ":material/battery_charging_full:")
    window = now_local - pd.Timedelta(days=min(period_days or 14, 14))
    seg = insights.state_segments(data["availability"], derive.charge_sessions(battery, battery_kwh()), pd.Timestamp.now(tz=LOCAL_TZ))
    if not seg.empty:
        seg = seg[seg["end"] >= window].assign(start=lambda d: d["start"].clip(lower=window))
    if seg.empty:
        empty("No state changes in the last 14 days.", ":material/timeline:")
    else:
        st.plotly_chart(activity_grid_chart(insights.activity_grid(seg, LOCAL_TZ), window.normalize(), now_local.normalize()),
                        width="stretch")
        st.caption("Each cell is one hour: blank when parked. "
                   "Colour deepens with the minutes. Events arrive only when the mode changes.")
    if not month.empty:
        st.subheader("By month")
        views.table(month)

# ---- path ----------------------------------------------------------------
with tab_path:
    with closing(connect(DB_PATH)) as ldb:
        saved_labels = place_labels.load(ldb)
    path.render(trip_table, location, config.load(), period_days, LOCAL_TZ, saved_labels, DB_PATH)

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
        cfg_a = config.load()
        status = config.air_status(cfg_a, last["pm25"])
        c = st.columns(4)
        c[0].metric("PM2.5", f"{last['pm25']:.0f}" if pd.notna(last["pm25"]) else "—", border=True,
                    delta=f"{views.AIR_LABEL[status]} (limits {cfg_a['air']['good']} and {cfg_a['air']['moderate']})" if status else None,
                    delta_color={"good": "normal", "moderate": "off", "high": "inverse"}.get(status, "off"),
                    delta_arrow="off",
                    help="Fine particles in the cabin. The API does not give the unit; assumed µg/m³. Good, Moderate and High "
                         "follow the WHO 2021 guideline values and can be changed under [air] in config.toml.")
        c[1].metric("Car's air index", f"{last['aqi']:.0f}" if pd.notna(last["aqi"]) else "—", border=True,
                    help="The car's own score. Its scale is undocumented and it tracks PM2.5 (about 1.4 times higher in the "
                         "stored readings), so it is not judged here.")
        if pd.notna(last["last_cycle"]):
            ago = pd.Timestamp.now(tz=LOCAL_TZ) - last["last_cycle"]
            c[2].metric("Last pre-cleaning", f"{ago.days} d ago" if ago.days else f"{int(ago.total_seconds() // 3600)} h ago",
                        border=True, help=f"{last['last_cycle']:%d %b %H:%M}")
            c[3].metric("Cycle valid", "Yes" if last["cycle_valid"] else "No", border=True)
        st.caption(f"Last reading {last['time']:%d %b %H:%M}. {len(air)} reading(s) stored. The car measures only now and then, "
                   "so these are spot readings, not a continuous record.")
        if len(air_v) < 2:
            empty("Trends appear once a few readings are stored (the car reports after each measurement).",
                  ":material/hourglass_empty:")
        else:
            pm = air_v.dropna(subset=["pm25"])
            fig = line_chart(pm, "time", "pm25", "PM2.5 readings", "PM2.5", mode="markers")
            fig.update_traces(hovertemplate="%{x|%d %b %H:%M}<br>%{y:.0f} PM2.5<extra></extra>",   # ring keeps stacked dots apart
                              marker=dict(opacity=0.85, line=dict(width=2, color=theme.palette()["surface"])))
            fig = views.air_bands(fig, cfg_a, float(pm["pm25"].max()))
            if xrange:
                fig.update_xaxes(range=xrange)
            st.plotly_chart(fig, width="stretch")
        daily = derive.air_daily(air_v, LOCAL_TZ)
        if len(daily) >= 2:
            st.plotly_chart(views.air_daily_chart(daily, cfg_a, xrange), width="stretch")
        if not daily.empty:
            st.subheader("Daily summary")
            views.table(daily.sort_values("date", ascending=False))
        cycles = derive.cleaning_cycles(air)
        if not cycles.empty:
            st.subheader("Pre-cleaning cycles")
            cycles["completed"] = cycles["completed"].dt.tz_convert(LOCAL_TZ)
            views.table(cycles)

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
    from polestar import mock_compare
    with tabs["Compare (mock)"]:
        mock_compare.render()
