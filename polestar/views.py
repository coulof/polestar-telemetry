"""Presentation helpers for the dashboard: Plotly figures, status chips and the charging notes.

Pure functions over data frames and config (no data loading), so they can be tested without running the app."""
import re
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from polestar import charging, config, theme


def rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


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
    fig = go.Figure(go.Bar(x=df[x], y=df[y], marker=dict(color=p["series"]),
                           hovertemplate="%{x}<br>%{y:.1f} " + ytitle + "<extra></extra>"))
    fig.update_layout(yaxis_title=ytitle, bargap=0.4)
    fig.update_xaxes(type="category")     # one label per bar; a date axis fans out with a single day
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


def state_timeline(seg):
    p = theme.palette()
    colors = {"Driving": p["series"], "Charging": p["good"], "Parked": rgba(p["muted"], 0.45)}
    fig = px.timeline(seg, x_start="start", x_end="end", y="state", color="state", color_discrete_map=colors,
                      category_orders={"state": ["Driving", "Charging", "Parked"]})
    fig.update_yaxes(title=None)
    fig.update_xaxes(title=None)
    return theme.style(fig, 220, "Driving, charging and parked time")


def empty(msg, icon=":material/hourglass_empty:"):
    st.info(msg, icon=icon)


def chips(exterior, health, pre, cfg, driving=False):
    """Coloured status badges (lock, doors, service, air) as one markdown line."""
    out = []
    if driving:     # lock and door events are hours old while the car is in use; don't show them as current
        out.append(":yellow-badge[:material/directions_car: In use]")
    elif not exterior.empty:
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


def fmt_daily(t):
    return f"{int(t.get('hour') or 0):02d}:{int(t.get('minute') or 0):02d}" if t else "—"


def charging_notes(db, b, charging_now, now, cfg, location):
    """Exceptions only: plug trouble, or charging at a peak rate while a cheaper window is coming."""
    notes = []
    target = charging.target_soc(charging.latest_payload(db, "targetSoc"))
    start = charging.next_timer_start(charging.latest_payload(db, "globalChargeTimer"), now.to_pydatetime())
    issue = charging.plug_issue(b, target, start.astimezone(ZoneInfo(cfg["timezone"])) if start else None)
    if issue:
        level, text = issue
        notes.append({"info": f":material/schedule: {text}", "warn": f":orange[:material/power_off: {text}]",
                      "error": f":red[:material/error: {text}]"}[level])
    if charging_now and not location.empty and "coordinate.latitude" in location:
        loc = config.match(cfg, location.iloc[-1]["coordinate.latitude"], location.iloc[-1]["coordinate.longitude"])
        if loc and loc.get("tariff"):
            local = now.to_pydatetime().astimezone(ZoneInfo(cfg["timezone"]))
            price = config.price_at(loc, local)
            if price > config.cheapest_price(loc, local):
                nxt = config.next_cheaper_start(loc, local)
                later = f"; the cheaper €{config.price_at(loc, nxt):.2f} rate starts at {nxt:%H:%M}" if nxt else ""
                notes.append(f":orange[:material/savings: Charging at the peak rate (€{price:.2f}/kWh){later}]")
    return notes
