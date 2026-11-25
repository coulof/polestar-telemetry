"""Presentation helpers for the dashboard: Plotly figures, status chips and the charging notes.

Pure functions over data frames and config (no data loading), so they can be tested without running the app."""
import re
from zoneinfo import ZoneInfo

import pandas as pd
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
        marker=dict(size=7 if mode == "markers" else 5, color=p["series"]),   # dots alone carry the data: a bit larger
        fill="tozeroy" if fill else None, fillcolor=rgba(p["series"], 0.10),
        hovertemplate="%{x|%d %b %H:%M}<br>%{y:.1f} " + ytitle + "<extra></extra>",
    ))
    fig.update_layout(yaxis_title=ytitle, hovermode="x unified")
    return theme.style(fig, height, title)


def day_axis(fig, days, xrange=None):
    """A date x-axis for one value per local day: the period when given (so it lines up with the time charts),
    otherwise the days found padded by one day (a lone day would otherwise fan out into hours)."""
    if xrange:
        rng = [pd.Timestamp(t).tz_localize(None) for t in xrange]      # the day values are naive local dates
        rng[1] = max(rng[1], days.max() + pd.Timedelta(hours=18))      # today's half-day bar is not cut off
    else:
        rng = [days.min() - pd.Timedelta(days=1), days.max() + pd.Timedelta(days=1)]
    return fig.update_xaxes(type="date", range=rng)


def bar_chart(df, x, y, title, ytitle, height=300, xrange=None):
    """One bar per local day, half a day wide on a date axis, so days without a value show as gaps."""
    p = theme.palette()
    days = pd.to_datetime(df[x])
    fig = go.Figure(go.Bar(x=days + pd.Timedelta(hours=12), y=df[y], width=43_200_000, marker=dict(color=p["series"]),
                           hovertemplate="%{x|%a %d %b}<br>%{y:.1f} " + ytitle + "<extra></extra>"))
    fig.update_layout(yaxis_title=ytitle)
    return day_axis(theme.style(fig, height, title), days, xrange)


def trip_line(t):
    """One trip for a tooltip: start time, places when known, distance, duration and the energy estimate."""
    where = f" {t['frm']} → {t['to']}" if isinstance(t.get("frm"), str) and isinstance(t.get("to"), str) else ""
    kwh = f" · ~{t['kwh']:.1f} kWh" if pd.notna(t.get("kwh")) else ""
    return f"{t['start']:%H:%M}{where} · {t['km']:.0f} km · {t['minutes']:.0f} min{kwh}"


def distance_chart(trips, xrange=None, height=300):
    """Distance per local day, one stacked segment per trip (same colour, split by a surface-coloured gap). Labels
    only the longest day and today, plus a dashed line at the average per calendar day. `trips` needs start, km and
    minutes; kwh, frm and to (place names) are used when present."""
    p = theme.palette()
    t = trips.sort_values("start").assign(day=lambda d: pd.to_datetime(d["start"].dt.date))
    t["n"] = t.groupby("day").cumcount()
    per_day = t.groupby("day").agg(km=("km", "sum"), trips=("km", "size"), minutes=("minutes", "sum"))
    head = {d: f"<b>{d:%a %d %b} · {r.km:.0f} km</b> · {int(r.trips)} trip{'s' if r.trips > 1 else ''} · "
               f"{int(r.minutes // 60)} h {int(r.minutes % 60):02d} min driving" for d, r in per_day.iterrows()}
    fig = go.Figure()
    for n, seg in t.groupby("n"):          # the n-th trip of each day: Plotly stacks traces that share an x
        fig.add_trace(go.Bar(
            x=seg["day"] + pd.Timedelta(hours=12), y=seg["km"], width=43_200_000,
            marker=dict(color=p["series"], line=dict(width=1, color=p["surface"])),
            customdata=list(zip(seg["day"].map(head), seg.apply(trip_line, axis=1))),
            hovertemplate="%{customdata[0]}<br>%{customdata[1]}<extra></extra>"))
    days = per_day.index
    if xrange:
        span = (pd.Timestamp(xrange[1]).normalize() - pd.Timestamp(xrange[0]).normalize()).days + 1
    else:
        span = (days.max() - days.min()).days + 1
    avg = per_day["km"].sum() / max(span, 1)
    fig.add_hline(y=avg, line=dict(color=p["baseline"], width=1, dash="dash"), annotation_text=f"avg {avg:.0f} km/day",
                  annotation_position="top left", annotation_font=dict(size=11, color=p["muted"]))
    today = pd.Timestamp(xrange[1]).tz_localize(None).normalize() if xrange else days.max()
    for d in dict.fromkeys([per_day["km"].idxmax()] + ([today] if today in days else [])):
        edge = d == today                    # today is the last bar: end the label at its right edge, not past the axis
        fig.add_annotation(x=d + pd.Timedelta(hours=18 if edge else 12), xanchor="right" if edge else "center",
                           y=per_day.loc[d, "km"], text=f"{per_day.loc[d, 'km']:.0f} km",
                           yanchor="bottom", showarrow=False, font=dict(size=12, color=p["ink"]))
    fig.update_layout(barmode="stack", bargap=0, yaxis_title="km", hovermode="closest")
    return day_axis(theme.style(fig, height, "Distance per day"), pd.Series(days), xrange)


def soc_gauge(soc):
    """Half-circle SoC gauge; a red tint below 20 % (low), a grey one above 80 % (not amber: it would sit next to the
    orange bar), ticks at the edges."""
    p = theme.palette()
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=soc, number=dict(suffix="%", font=dict(size=44, color=p["ink"])),
        gauge=dict(
            axis=dict(range=[0, 100], tickvals=[0, 20, 80, 100], tickfont=dict(size=12, color=p["muted"]),
                      tickcolor=p["muted"]),
            bar=dict(color=p["series"], thickness=0.3), bgcolor="rgba(0,0,0,0)", borderwidth=0,
            steps=[dict(range=[0, 20], color=rgba(p["low"], 0.28)),
                   dict(range=[20, 80], color=p["grid"]),
                   dict(range=[80, 100], color=p["rest"])],
        ),
    ))
    return theme.style(fig, 200).update_layout(margin=dict(l=30, r=36, t=10, b=0))


def activity_grid_chart(grid, first, last):
    """One row per local day from `first` to `last`, one column per hour; a cell is coloured only when the car was
    driven or charging in that hour (parked leaves it blank)."""
    p = theme.palette()
    floor = 0.6 if theme.is_dark() else 0.45     # the faintest step stays visible on the dark surface
    days = [d.date() for d in pd.date_range(first, last)]
    fig = go.Figure()
    for state, colour in (("Driving", p["series"]), ("Charging", p["good"])):
        g = grid[grid["state"] == state]
        z = [[None] * 24 for _ in days]
        for _, r in g.iterrows():
            if r["date"] in days:
                z[days.index(r["date"])][int(r["hour"])] = r["minutes"]
        fig.add_trace(go.Heatmap(
            z=z, x=list(range(24)), y=[f"{d:%a %d %b}" for d in days], name=state, showscale=False, xgap=1, ygap=2,
            zmin=0, zmax=60, colorscale=[[0, rgba(colour, floor)], [1, colour]], hoverongaps=False,
            hovertemplate="%{y}, %{x}:00<br>%{z:.0f} min " + state.lower() + "<extra></extra>"))
    for state, colour in (("Driving", p["series"]), ("Charging", p["good"])):
        # heatmaps have no legend entry of their own: an empty square marker names the colour
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=state, showlegend=True,
                                 marker=dict(symbol="square", size=11, color=colour)))
    fig.update_xaxes(tickmode="linear", dtick=3, ticksuffix=":00")
    fig.update_yaxes(type="category", autorange="reversed", showgrid=False)
    return theme.style(fig, max(160, 26 * len(days) + 60), "Driving and charging, by day and hour").update_layout(
        showlegend=True, legend=dict(orientation="h", x=0, yref="container", y=0, yanchor="bottom", font=dict(size=12)),   # below the hour labels:
        margin=dict(b=60))                                                                                # no clash with the title on phones


AIR_LABEL = {"good": "Good", "moderate": "Moderate", "high": "High"}
AIR_COLOUR = {"good": "good", "moderate": "high", "high": "low"}      # theme.palette() keys: green, amber, red


def air_bands(fig, cfg, top):
    """Shade the Good / Moderate / High PM2.5 bands behind a chart; `top` is the highest value on the y axis."""
    p = theme.palette()
    lim = cfg["air"]
    for lo, hi, key in ((0, lim["good"], "good"), (lim["good"], lim["moderate"], "moderate"), (lim["moderate"], max(top, lim["moderate"] * 1.2), "high")):
        fig.add_hrect(y0=lo, y1=hi, fillcolor=rgba(p[AIR_COLOUR[key]], 0.10), line_width=0, layer="below",
                      annotation_text=AIR_LABEL[key], annotation_position="top left",
                      annotation_font=dict(size=11, color=p["muted"]))
    fig.update_yaxes(range=[0, max(top, lim["moderate"] * 1.2)])
    return fig


def air_daily_chart(daily, cfg, xrange=None):
    """Daily PM2.5: the min-max range as a grey band, the mean as a line, over the Good / Moderate / High bands.
    Days sit at local noon on the same period axis as the readings chart above it."""
    p = theme.palette()
    days = pd.to_datetime(daily["date"])
    x = days + pd.Timedelta(hours=12)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=daily["pm25_max"], mode="lines", line=dict(width=0), hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=daily["pm25_min"], mode="lines", line=dict(width=0), fill="tonexty",
                             fillcolor=rgba(p["baseline"], 0.12), hoverinfo="skip"))   # grey, so it does not tint the bands
    fig.add_trace(go.Scatter(x=x, y=daily["pm25_avg"], mode="lines+markers", line=dict(color=p["series"], width=2),
                             marker=dict(size=5, color=p["series"]), customdata=daily[["pm25_min", "pm25_max"]],
                             hovertemplate="%{x|%a %d %b}<br>mean %{y:.1f}, min %{customdata[0]:.0f}, max %{customdata[1]:.0f}<extra></extra>"))
    fig.update_layout(yaxis_title="PM2.5 (assumed µg/m³)", hovermode="x unified")
    fig = day_axis(theme.style(fig, 300, "PM2.5 per day: mean and range"), days, xrange)
    return air_bands(fig, cfg, float(daily["pm25_max"].max()))


COLUMNS = {   # readable headers and units for the tables; keys are the data frame column names
    "start": st.column_config.DatetimeColumn("Start", format="D MMM HH:mm"),
    "end": st.column_config.DatetimeColumn("End", format="D MMM HH:mm"),
    "completed": st.column_config.DatetimeColumn("Completed", format="D MMM HH:mm"),
    "date": st.column_config.DateColumn("Day", format="ddd D MMM"),
    "month": st.column_config.TextColumn("Month"),
    "km": st.column_config.NumberColumn("km", format="%.1f"),
    "minutes": st.column_config.NumberColumn("Minutes", format="%.0f"),
    "avg_kmh": st.column_config.NumberColumn("Avg km/h", format="%.0f"),
    "soc_start": st.column_config.NumberColumn("SoC start", format="%.0f %%"),
    "soc_end": st.column_config.NumberColumn("SoC end", format="%.0f %%"),
    "kwh": st.column_config.NumberColumn("kWh (est.)", format="%.1f"),
    "kwh_per_100km": st.column_config.NumberColumn("kWh/100 km (est.)", format="%.1f"),
    "kwh_from_soc": st.column_config.NumberColumn("kWh from SoC", format="%.1f"),
    "kwh_from_power": st.column_config.NumberColumn("kWh from power", format="%.1f"),
    "peak_kw": st.column_config.NumberColumn("Peak kW", format="%.1f"),
    "type": st.column_config.TextColumn("Type"),
    "median_amps": st.column_config.NumberColumn("Median A", format="%.0f"),
    "partial": st.column_config.CheckboxColumn("Start missed"),
    "location": st.column_config.TextColumn("Location"),
    "location_manual": st.column_config.CheckboxColumn("Location set manually"),
    "fee": st.column_config.NumberColumn("Session fee", format="€%.2f"),
    "price_per_kwh": st.column_config.NumberColumn("€/kWh", format="€%.2f"),
    "cost": st.column_config.NumberColumn("Cost", format="€%.2f"),
    "cost_cheapest": st.column_config.NumberColumn("At cheapest rate", format="€%.2f"),
    "saving": st.column_config.NumberColumn("Missed saving", format="€%.2f"),
    "manual": st.column_config.CheckboxColumn("Entered manually"),
    "sessions": st.column_config.NumberColumn("Sessions"),
    "cost_per_100km": st.column_config.NumberColumn("Cost per 100 km", format="€%.2f"),
    "co2_kg": st.column_config.NumberColumn("CO2 (kg)", format="%.1f"),
    "co2_g_per_km": st.column_config.NumberColumn("CO2 (g/km)", format="%.0f"),
    "aqi_avg": st.column_config.NumberColumn("Air index, mean", format="%.1f"),
    "aqi_max": st.column_config.NumberColumn("Air index, max", format="%.0f"),
    "pm25_min": st.column_config.NumberColumn("PM2.5 min", format="%.0f"),
    "pm25_avg": st.column_config.NumberColumn("PM2.5 mean", format="%.1f"),
    "pm25_max": st.column_config.NumberColumn("PM2.5 max", format="%.0f"),
    "readings": st.column_config.NumberColumn("Readings"),
    "valid": st.column_config.CheckboxColumn("Valid"),
    "time there (h)": st.column_config.NumberColumn("Time there (h)", format="%.0f"),
    "arrivals": st.column_config.NumberColumn("Arrivals"),
    "km driven to": st.column_config.NumberColumn("km driven to", format="%.0f"),
    "% of time": st.column_config.ProgressColumn("Share of time", min_value=0, max_value=100, format="%.0f %%"),
}


def table(df):
    """st.dataframe with the shared column labels and number formats."""
    st.dataframe(df, hide_index=True, width="stretch", column_config={c: COLUMNS[c] for c in df.columns if c in COLUMNS})


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
        pm = pd.to_numeric(q.get("measuredParticulateMatter25"), errors="coerce")
        status = config.air_status(cfg, pm)
        if status:
            color = {"good": "green", "moderate": "orange", "high": "red"}[status]
            out.append(f":{color}-badge[:material/air: Air {AIR_LABEL[status].lower()} · PM2.5 {pm:.0f}]")
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
