"""MOCK-UP of a month-to-month "Compare" view, drawn from synthetic data.

Shown only when POLESTAR_MOCK=1 so the design can be judged before real history exists.
Nothing here reads the database. Delete this file and its hook in app.py to remove it."""
import datetime as dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import theme

# key -> (label, unit, decimals). Axes of the radar and the metric toggles.
METRICS = {
    "km": ("Distance", "km", 0), "trips": ("Trips", "", 0), "drive_h": ("Driving time", "h", 1),
    "kwh": ("Energy charged", "kWh", 0), "cost": ("Charging cost", "€", 0), "days": ("Days used", "", 0),
}
TOGGLE = {"Distance": "km", "Energy": "kwh", "Cost": "cost", "Trips": "trips"}
# one hue per metric, light -> dark (sequential): (light mode, dark mode), each (lowest, highest)
HUES = {
    "km": (("#FCE9A6", "#F6BE00"), ("#4a3b08", "#FFCC1A")),        # Swedish gold
    "trips": (("#FCE9A6", "#F6BE00"), ("#4a3b08", "#FFCC1A")),
    "kwh": (("#c8f0dc", "#0fa36b"), ("#14392b", "#3ddc9a")),
    "cost": (("#d3e3f8", "#1f5fb0"), ("#1b2d4a", "#6ea8ff")),
}


@st.cache_data
def synth(today):
    """~13 months of plausible daily usage: weekday commutes, quiet weeks, weekend trips, winter consumption."""
    rng = np.random.default_rng(7)
    rows, debt = [], 0.0
    for d in pd.date_range(today - dt.timedelta(days=400), today):
        away = (d.day_of_year // 7) % 11 == 3                     # roughly one week away every 11 weeks
        used = (not away) and rng.random() < (0.88 if d.weekday() < 5 else 0.5)
        km = max(0.0, rng.normal(40 if d.weekday() < 5 else 55, 14)) if used else 0.0
        if used and d.weekday() >= 5 and rng.random() < 0.15:
            km += rng.uniform(80, 220)                          # an occasional long weekend drive
        cons = 15.5 * (1 + 0.25 * np.cos((d.day_of_year - 15) / 365 * 2 * np.pi))   # kWh/100 km, worse in winter
        debt += km * cons / 100 / 0.9
        kwh = cost = 0.0
        if debt > 35:                                           # plug in after about half a pack
            kwh, debt = debt * rng.uniform(0.9, 1.0), 0.0
            price = rng.choice([0.1589, 0.2142], p=[0.65, 0.35]) if rng.random() < 0.85 else 0.45
            cost = kwh * price
        trips = 0 if km == 0 else (2 if km < 70 else 3) + int(rng.random() < 0.2)
        rows.append(dict(date=d.normalize(), km=km, trips=trips, drive_h=km / 46, kwh=kwh, cost=cost, days=int(km > 0)))
    return pd.DataFrame(rows)


def window(df, a, b):
    return df[(df["date"] >= pd.Timestamp(a)) & (df["date"] <= pd.Timestamp(b))].reset_index(drop=True)


def totals(w):
    t = {k: float(w[k].sum()) for k in METRICS}
    t["kwh100"] = t["kwh"] / t["km"] * 100 if t["km"] else float("nan")
    t["eur100"] = t["cost"] / t["km"] * 100 if t["km"] else float("nan")
    return t


def fmt(key, v):
    label, unit, dec = METRICS[key]
    return f"{v:,.{dec}f} {unit}".strip()


def baseline_grey():
    """Brand grey for the comparison period: Grey 9 on light surfaces, Grey 5 on dark."""
    return "#B1B3B3" if theme.is_dark() else "#75787B"


def rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def delta_cards(ta, tb, la, lb):
    cards = [("Distance", "km", ta["km"], tb["km"], "off", "{:,.0f} km"),
             ("Trips", "trips", ta["trips"], tb["trips"], "off", "{:,.0f}"),
             ("Energy charged", "kwh", ta["kwh"], tb["kwh"], "off", "{:,.0f} kWh"),
             ("Charging cost", "cost", ta["cost"], tb["cost"], "inverse", "€{:,.0f}"),
             ("kWh per 100 km (est.)", "kwh100", ta["kwh100"], tb["kwh100"], "inverse", "{:.1f}"),
             ("Cost per 100 km", "eur100", ta["eur100"], tb["eur100"], "inverse", "€{:.2f}")]
    for row in (cards[:3], cards[3:]):
        for col, (label, _, a, b, color, f) in zip(st.columns(3), row):
            pct = (a - b) / b * 100 if b else None
            col.metric(label, f.format(a), f"{pct:+.0f}% vs {lb}" if pct is not None and np.isfinite(pct) else None,
                       delta_color=color, border=True)


def cumulative(wa, wb, key, la, lb):
    p = theme.palette()
    label, unit, _ = METRICS[key]
    fig = go.Figure()
    for w, name, color, width in ((wb, lb, baseline_grey(), 2), (wa, la, p["series"], 3)):
        y = w[key].cumsum()
        fig.add_trace(go.Scatter(x=w["date"].dt.day, y=y, mode="lines", name=name, line=dict(color=color, width=width),
                                 hovertemplate="day %{x}: %{y:,.0f} " + unit + f"<extra>{name}</extra>"))
        fig.add_annotation(x=w["date"].dt.day.iloc[-1], y=y.iloc[-1], text=f"{name}  {y.iloc[-1]:,.0f}", showarrow=False,
                           xanchor="left", xshift=6, font=dict(color=color, size=12))
    fig.update_layout(xaxis_title="day of month", yaxis_title=unit, hovermode="x unified")
    fig.update_xaxes(dtick=5)
    return theme.style(fig, 400, f"{label}, cumulative").update_layout(showlegend=True, legend=dict(orientation="h", y=-0.25),
                                                                       margin=dict(l=0, r=90, t=40, b=0))


def radar(ta, tb, la, lb):
    p = theme.palette()
    keys = list(METRICS)
    short = {"drive_h": "Driving", "kwh": "Energy", "cost": "Cost"}   # short axis labels so none are clipped
    cats = [short.get(k, METRICS[k][0]) for k in keys]
    top = [max(ta[k], tb[k]) or 1 for k in keys]                  # each axis: the larger month touches the rim
    fig = go.Figure()
    for t, name, color in ((tb, lb, baseline_grey()), (ta, la, p["series"])):
        r = [t[k] / m * 100 for k, m in zip(keys, top)]
        real = [fmt(k, t[k]) for k in keys]
        fig.add_trace(go.Scatterpolar(
            r=r + r[:1], theta=cats + cats[:1], fill="toself", name=name, customdata=real + real[:1],
            line=dict(color=color, width=2), fillcolor=rgba(color, 0.22 if color == p["series"] else 0.12),
            hovertemplate="%{theta}: %{customdata}<extra>" + name + "</extra>"))
    fig.update_layout(polar=dict(
        bgcolor="rgba(0,0,0,0)", radialaxis=dict(range=[0, 100], showticklabels=False, gridcolor=p["grid"], linecolor=p["grid"]),
        angularaxis=dict(gridcolor=p["grid"], linecolor=p["grid"], tickfont=dict(size=12, color=p["muted"]))))
    return theme.style(fig, 340, "Shape of the month").update_layout(
        showlegend=True, legend=dict(orientation="h", y=-0.12), margin=dict(l=80, r=80, t=40, b=20))


def heat_colors(key):
    lo, hi = HUES[key][1 if theme.is_dark() else 0]
    empty = "#2f3236" if theme.is_dark() else "#ECECE7"   # brand Grey 0 on light
    return [[0, empty], [0.001, lo], [1, hi]]


def calendar(df, key, today):
    """GitHub-style calendar: weeks across, weekdays down, one cell per day."""
    label, unit, dec = METRICS[key]
    start = today - dt.timedelta(days=364)
    start -= dt.timedelta(days=start.weekday())
    days = pd.date_range(start, today)
    s = df.set_index("date")[key].reindex(days)
    weeks = (len(days) + 6) // 7 + (1 if days[-1].weekday() != 6 else 0)
    z = np.full((7, weeks), np.nan)
    txt = np.full((7, weeks), "", dtype=object)
    for d, v in s.items():
        w = (d - pd.Timestamp(start)).days // 7
        z[d.weekday(), w] = v
        txt[d.weekday(), w] = f"{d:%a %d %b %Y}: {v:,.{dec}f} {unit}".strip()
    xs = [pd.Timestamp(start) + dt.timedelta(days=7 * w) for w in range(weeks)]
    first = [x for x in xs if x.day <= 7]
    fig = go.Figure(go.Heatmap(z=z, x=xs, y=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], text=txt, hoverinfo="text",
                               colorscale=heat_colors(key), xgap=3, ygap=3, showscale=False, zmin=0, zmax=np.nanpercentile(z, 98)))
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(size=11))
    fig.update_xaxes(tickmode="array", tickvals=first, ticktext=[f"{x:%b}" for x in first], showgrid=False, side="top")
    return theme.style(fig, 210, None).update_layout(margin=dict(l=0, r=0, t=24, b=0))


def monthly_bars(df, key, today):
    label, unit, dec = METRICS[key]
    m = df.assign(month=df["date"].dt.to_period("M")).groupby("month")[key].sum().tail(13)
    top = HUES[key][1 if theme.is_dark() else 0][1]
    colors = [top if i < len(m) - 1 else rgba(top, 0.45) for i in range(len(m))]   # lighter = month in progress
    fig = go.Figure(go.Bar(x=[f"{x:%b %y}" for x in m.index.to_timestamp()], y=m.values, marker=dict(color=colors),
                           hovertemplate="%{x}: %{y:,.0f} " + unit + "<extra></extra>"))
    fig.update_layout(bargap=0.35, yaxis_title=unit)
    return theme.style(fig, 260, f"{label} per month")


def hour_grid(df, a0, a1):
    """Day-by-hour state grid for one month (synthetic hours derived from the daily totals)."""
    w = window(df, a0, a1)
    z = np.zeros((len(w), 24))
    for i, r in w.iterrows():
        if r["km"] > 0:
            hrs = [8, 18] if r["date"].weekday() < 5 else [11, 12, 15]
            for h in hrs[: max(1, int(round(r["drive_h"])) + 1)]:
                z[i, h] = 1
        if r["kwh"] > 0:
            z[i, 0:7] = 2
    p = theme.palette()
    parked = rgba(p["muted"], 0.18)
    fig = go.Figure(go.Heatmap(z=z, x=list(range(24)), y=[f"{d:%d}" for d in w["date"]], xgap=1, ygap=2, showscale=False, zmin=0, zmax=2,
                               colorscale=[[0, parked], [0.33, parked], [0.34, p["series"]], [0.66, p["series"]], [0.67, p["good"]], [1, p["good"]]],
                               hovertemplate="day %{y}, %{x}:00<extra></extra>"))
    fig.update_yaxes(autorange="reversed", showgrid=False, dtick=2)
    fig.update_xaxes(showgrid=False, dtick=3, title="hour of day")
    return theme.style(fig, 520, None)


def complete_months(df, today):
    """Distance per month for the last 12 complete months (the month in progress is excluded)."""
    first = pd.Timestamp(today.replace(day=1))
    past = df[(df["date"] < first) & (df["date"] >= first - pd.DateOffset(months=12))]
    return past.assign(month=past["date"].dt.to_period("M")), past.assign(month=past["date"].dt.to_period("M")).groupby("month")["km"].sum()


def baseline(df, today, choice, wa, a_month, mode, b_prev):
    """(frame, long label, short label) of the comparison period for `choice`, over the same days as `wa`."""
    past, per_month = complete_months(df, today)
    pool = per_month[per_month.index != a_month]
    n = len(wa)
    if choice == "Previous month":
        return None, None, None
    if choice == "Average month":
        daily = past[past["month"].isin(pool.index)].assign(dom=lambda d: d["date"].dt.day).groupby("dom")[list(METRICS)].mean()
        daily = daily.loc[daily.index <= n].reset_index()
        daily["date"] = pd.to_datetime(dict(year=2000, month=1, day=daily["dom"]))
        return daily.drop(columns="dom"), "12-month average", "average"
    pm = pool.idxmax() if choice == "Busiest month" else pool.idxmin()
    b0 = pm.start_time.date()
    b1 = b0 + dt.timedelta(days=n - 1) if mode == "Month to date" else pm.end_time.date()
    word = "Busiest" if choice == "Busiest month" else "Quietest"
    return window(df, b0, b1), f"{word} ({pm:%b %Y})", word.lower()


def render():
    today = dt.date.today()
    df = synth(today)
    st.warning("Mock-up on synthetic data, only to judge the design. Nothing on this tab is your real usage.",
               icon=":material/science:")
    c1, c2 = st.columns([1, 2.2])
    with c1:
        mode = st.segmented_control("Compare", ["Month to date", "Full months"], default="Month to date", key="cmp_mode") or "Month to date"
    with c2:
        choice = st.segmented_control("Against", ["Previous month", "Busiest month", "Quietest month", "Average month"],
                                      default="Previous month", key="cmp_against") or "Previous month"
    first = today.replace(day=1)
    if mode == "Month to date":
        a0, a1 = first, today
        la = f"{a0:%b} 1-{today.day}"
        b0 = (first - dt.timedelta(days=1)).replace(day=1)
        b1 = b0 + dt.timedelta(days=today.day - 1)          # the same number of days, so the comparison is fair
    else:
        months = []
        d = first
        for _ in range(12):
            d = (d - dt.timedelta(days=1)).replace(day=1)
            months.append(d)
        a0 = st.selectbox("Month", months, 0, format_func=lambda m: f"{m:%B %Y}")
        a1 = (a0 + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        la = f"{a0:%b %Y}"
        b0 = (a0 - dt.timedelta(days=1)).replace(day=1)
        b1 = (b0 + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    wa = window(df, a0, a1)
    wb = window(df, b0, b1)
    lb = f"{b0:%b} 1-{b1.day}" if mode == "Month to date" else f"{b0:%b %Y}"
    short = lb
    if choice != "Previous month":
        frame, long_label, short_label = baseline(df, today, choice, wa, pd.Period(a0, "M"), mode, wb)
        wb, lb, short = frame, long_label, short_label
    ta, tb = totals(wa), totals(wb)

    st.subheader(f"{la} vs {lb}")
    delta_cards(ta, tb, la, short)

    pick = st.segmented_control("Metric", list(TOGGLE), default="Distance", key="cmp_metric", label_visibility="collapsed") or "Distance"
    st.plotly_chart(cumulative(wa, wb, TOGGLE[pick], la, lb), width="stretch", config={"displayModeBar": False})

    left, right = st.columns(2, vertical_alignment="center")
    left.plotly_chart(radar(ta, tb, la, lb), width="stretch", config={"displayModeBar": False})
    with right:
        rows = []
        for k in METRICS:
            pct = (ta[k] - tb[k]) / tb[k] * 100 if tb[k] else float("nan")
            rows.append({"": METRICS[k][0], la: fmt(k, ta[k]), lb: fmt(k, tb[k]), "change": f"{pct:+.0f}%" if np.isfinite(pct) else "—"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("Radar axes are scaled so the larger of the two periods touches the rim; hover for real values.")

    st.subheader("The year")
    ykey = TOGGLE[st.segmented_control("Year metric", list(TOGGLE), default="Distance", key="cmp_year", label_visibility="collapsed") or "Distance"]
    st.plotly_chart(calendar(df, ykey, today), width="stretch", config={"displayModeBar": False})
    st.caption("One cell per day, darker means more. Empty days are light grey.")
    st.plotly_chart(monthly_bars(df, ykey, today), width="stretch", config={"displayModeBar": False})

    st.subheader(f"{la.split(' ')[0]} hour by hour")
    st.markdown(":orange-badge[Driving] :green-badge[Charging] :gray-badge[Parked]")
    st.plotly_chart(hour_grid(df, a0, a1), width="stretch", config={"displayModeBar": False})
