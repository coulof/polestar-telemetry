"""MOCK-UP of a "Hero's Path" view: a map trail you can scrub or replay day by day, a stays timeline
and a places table. Synthetic data on made-up places on a generic coastal map.

Shown only when POLESTAR_MOCK=1. Nothing here reads the database. Delete this file and its hook in app.py
to remove it.

Real data limits: the car reports a position only when parked (not while driving), so a real trail would be
curves drawn between parked positions, never the actual road."""
import datetime as dt
import math

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from . import theme

# made-up places (lat, lon)
PLACES = {"Home": (57.7030, 11.9660), "Work": (57.7400, 12.0720), "Gym": (57.6900, 12.0100),
          "Supermarket": (57.7120, 11.9400), "Parents": (57.5000, 11.7500), "Beach house": (57.7960, 10.5200)}


def km_between(a, b):
    (la1, lo1), (la2, lo2) = PLACES[a], PLACES[b]
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h)) * 1.3          # straight line x 1.3 for roads


@st.cache_data
def synth_trips(today, days=60):
    """Commutes, errands, the odd weekend away: [start, end, frm, to, km]."""
    rng = np.random.default_rng(11)
    rows, here = [], "Home"

    def add(day, hour, to):
        nonlocal here
        start = pd.Timestamp(day) + pd.Timedelta(hours=hour, minutes=int(rng.integers(-15, 15)))
        km = km_between(here, to)
        rows.append(dict(start=start, end=start + pd.Timedelta(minutes=km / 45 * 60 + 3), frm=here, to=to, km=km))
        here = to

    for d in pd.date_range(today - dt.timedelta(days=days - 1), today):
        wd = d.weekday()
        if here == "Beach house" and wd == 6:
            add(d, 17.0, "Home")
        elif wd < 5 and rng.random() < 0.88:
            add(d, 7.8, "Work")
            r = rng.random()
            if r < 0.25:
                add(d, 17.8, "Gym"); add(d, 19.5, "Home")
            elif r < 0.4:
                add(d, 17.6, "Supermarket"); add(d, 18.4, "Home")
            else:
                add(d, 17.7, "Home")
        elif wd == 5 and rng.random() < 0.35 and d < pd.Timestamp(today):
            add(d, 9.5, "Beach house")
        elif wd >= 5 and rng.random() < 0.6:
            errand = "Supermarket" if rng.random() < 0.5 else "Gym"
            add(d, 10.5, errand); add(d, 11.6, "Home")
        elif wd == 6 and rng.random() < 0.12:
            add(d, 11.0, "Parents"); add(d, 16.5, "Home")
    return pd.DataFrame(rows)


def curve(a, b, n=14):
    """A gentle arc from place a to place b (the real road is unknown, so this is only a guess)."""
    (la1, lo1), (la2, lo2) = PLACES[a], PLACES[b]
    k = math.cos(math.radians((la1 + la2) / 2))
    dx, dy = (lo2 - lo1) * k, la2 - la1
    side = 1 if a < b else -1                                  # opposite directions bow opposite ways
    cx, cy = (lo1 * k + lo2 * k) / 2 - dy * 0.16 * side, (la1 + la2) / 2 + dx * 0.16 * side
    lats, lons = [], []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * lo1 * k + 2 * (1 - t) * t * cx + t ** 2 * lo2 * k
        y = (1 - t) ** 2 * la1 + 2 * (1 - t) * t * cy + t ** 2 * la2
        lats.append(y); lons.append(x / k)
    return lats, lons


def lines(trips):
    lat, lon = [], []
    for _, t in trips.iterrows():
        la, lo = curve(t["frm"], t["to"])
        lat += la + [None]; lon += lo + [None]
    return lat, lon


def stays(trips, window_start, now):
    """Rows for the stays timeline: (start, end, row, kind)."""
    here = trips["frm"].iloc[0] if len(trips) else "Home"     # where the car stood before its first trip in view
    rows, since = [], pd.Timestamp(window_start)
    for _, t in trips.iterrows():
        rows.append((since, t["start"], here, "Stay"))
        rows.append((t["start"], t["end"], "Driving", "Driving"))
        here, since = t["to"], t["end"]
    rows.append((since, now, here, "Stay"))
    df = pd.DataFrame(rows, columns=["start", "end", "row", "kind"])
    df = df[df["end"] > pd.Timestamp(window_start)].copy()
    df["start"] = df["start"].clip(lower=pd.Timestamp(window_start))
    return df


def fit_view(lat, lon, width=1040, height=520, pad=0.7, lo=6.5, hi=12.5):
    """Map centre and zoom that frame the given points (web-mercator, with some padding)."""
    pts = [(a, b) for a, b in zip(lat, lon) if a is not None]
    la0, la1 = min(a for a, _ in pts), max(a for a, _ in pts)
    lo0, lo1 = min(b for _, b in pts), max(b for _, b in pts)
    cy = (la0 + la1) / 2
    lon_span, lat_span = max(lo1 - lo0, 1e-3), max(la1 - la0, 1e-3)
    z = min(math.log2(pad * width * 360 / (512 * lon_span)),          # MapLibre uses 512 px tiles
            math.log2(pad * height * 360 * math.cos(math.radians(cy)) / (512 * lat_span)))
    return dict(lat=cy, lon=(lo0 + lo1) / 2), max(lo, min(hi, z))


def trail_figure(trips, days, zoom="Follow"):
    p = theme.palette()
    dark = theme.is_dark()
    visits = trips["to"].value_counts()
    names = list(PLACES)
    fig = go.Figure()
    fig.add_trace(go.Scattermap(
        lat=[PLACES[n][0] for n in names], lon=[PLACES[n][1] for n in names], mode="markers+text", text=names,
        textposition="top right", textfont=dict(size=12, color=p["ink"]),
        marker=dict(size=[9 + 3 * math.sqrt(visits.get(n, 0)) for n in names], color=p["muted"]),
        hovertext=[f"{n}: {visits.get(n, 0)} arrivals in this period" for n in names], hoverinfo="text", name="places"))

    def day_traces(i):
        d = days[i]
        day = trips["start"].dt.normalize()
        old = trips[day < d - pd.Timedelta(days=6)]
        recent = trips[(day >= d - pd.Timedelta(days=6)) & (day < d)]
        today = trips[day == d]
        upto = trips[day <= d]
        here = upto["to"].iloc[-1] if len(upto) else (trips["frm"].iloc[0] if len(trips) else "Home")
        old_l, recent_l, today_l = lines(old), lines(recent), lines(today)    # each curve set built once
        # the camera jumps to where the day happened: origin, destination and everything in between
        view = fit_view(*today_l) if len(today) else fit_view([PLACES[here][0]] * 2, [PLACES[here][1]] * 2, hi=10.5, lo=10.5)
        return [
            go.Scattermap(lat=old_l[0], lon=old_l[1], mode="lines", line=dict(width=2, color=p["faint"]), name="older", hoverinfo="skip"),
            go.Scattermap(lat=recent_l[0], lon=recent_l[1], mode="lines", line=dict(width=3, color=p["baseline"]), name="last 7 days", hoverinfo="skip"),
            go.Scattermap(lat=today_l[0], lon=today_l[1], mode="lines", line=dict(width=5, color=p["series"]), name="that day", hoverinfo="skip"),
            go.Scattermap(lat=[PLACES[here][0]], lon=[PLACES[here][1]], mode="markers", marker=dict(size=19, color=p["series"]), name="car", hoverinfo="skip"),
        ], f"{d:%a %d %b}  ·  {len(today)} trip(s)  ·  {today['km'].sum():.0f} km", view

    last = len(days) - 1
    initial, last_label, last_view = day_traces(last)
    for t in initial:
        fig.add_trace(t)
    frames = []
    for i in range(len(days)):
        data, label, view = day_traces(i)
        cam = dict(map=dict(center=view[0], zoom=view[1])) if zoom == "Follow" else {}
        frames.append(go.Frame(name=str(i), data=data, traces=[1, 2, 3, 4], layout=go.Layout(**cam, annotations=[dict(
            text=label, x=0.01, y=0.98, xref="paper", yref="paper", xanchor="left", yanchor="top", showarrow=False,
            font=dict(size=15, color=p["ink"]), bgcolor="rgba(0,0,0,0.0)")])))
    fig.frames = frames
    steps = [dict(method="animate", label=f"{d:%d %b}" if (i % 5 == 0 or i == last) else "",
                  args=[[str(i)], dict(mode="immediate", frame=dict(duration=0, redraw=True), transition=dict(duration=0))])
             for i, d in enumerate(days)]
    fig.update_layout(
        map=dict(style="carto-darkmatter" if dark else "carto-positron",
                 center=last_view[0] if zoom == "Follow" else dict(lat=57.71, lon=11.99) if zoom == "City" else dict(lat=57.62, lon=11.37),
                 zoom=last_view[1] if zoom == "Follow" else 10.2 if zoom == "City" else 7.3),
        sliders=[dict(active=last, steps=steps, x=0.14, len=0.85, y=0.0, pad=dict(t=10, b=0), currentvalue=dict(visible=False),
                      bgcolor=p["grid"], activebgcolor=p["series"], bordercolor=p["grid"], tickcolor=p["muted"],
                      font=dict(color=p["muted"], size=11))],
        updatemenus=[dict(type="buttons", direction="left", x=0.0, y=0.0, xanchor="left", yanchor="top", pad=dict(t=8), showactive=False,
                          buttons=[dict(label="Play", method="animate", args=[None, dict(frame=dict(duration=420, redraw=True), fromcurrent=True, transition=dict(duration=0))]),
                                   dict(label="Pause", method="animate", args=[[None], dict(mode="immediate", frame=dict(duration=0, redraw=False), transition=dict(duration=0))])])],
        annotations=[dict(text=last_label, x=0.01, y=0.98, xref="paper", yref="paper",
                          xanchor="left", yanchor="top", showarrow=False, font=dict(size=15, color=p["ink"]))],
        legend=dict(orientation="h", y=1.02, x=1, xanchor="right", font=dict(size=12, color=p["muted"])), showlegend=True)
    return theme.style(fig, 600).update_layout(showlegend=True, margin=dict(l=0, r=0, t=0, b=64))


def timeline_figure(trips, now, days=7):
    p = theme.palette()
    start = (pd.Timestamp(now) - pd.Timedelta(days=days)).normalize()
    df = stays(trips[trips["end"] >= start], start, pd.Timestamp(now))
    order = ["Driving"] + [n for n in PLACES if n in set(df["row"])]
    fig = px.timeline(df, x_start="start", x_end="end", y="row", color="kind", category_orders={"row": order},
                      color_discrete_map={"Stay": p["baseline"], "Driving": p["series"]})
    fig.update_traces(marker=dict(cornerradius=4))
    fig.update_yaxes(title=None, autorange="reversed")
    fig.update_xaxes(title=None)
    return theme.style(fig, 300, "Where the car was, last 7 days").update_layout(showlegend=False)


def places_table(trips, now, days):
    start = (pd.Timestamp(now) - pd.Timedelta(days=days)).normalize()
    df = stays(trips[trips["end"] >= start], start, pd.Timestamp(now))
    df = df[df["kind"] == "Stay"].assign(hours=lambda d: (d["end"] - d["start"]).dt.total_seconds() / 3600)
    arrivals = trips[trips["end"] >= start].groupby("to").agg(arrivals=("km", "size"), km_in=("km", "sum"))
    out = df.groupby("row").agg(hours=("hours", "sum")).join(arrivals, how="outer").fillna(0).reset_index(names="place")
    out["share"] = out["hours"] / max(out["hours"].sum(), 1) * 100
    return out.sort_values("hours", ascending=False).assign(
        hours=lambda d: d["hours"].round(0), km_in=lambda d: d["km_in"].round(0), share=lambda d: d["share"].round(0)
    ).rename(columns={"hours": "time there (h)", "km_in": "km driven to", "share": "% of time"})


def render():
    now = pd.Timestamp.now()
    today = now.date()
    st.warning("Mock-up on synthetic data and made-up places, only to judge the design. Nothing here is your real history.",
               icon=":material/science:")
    span = st.segmented_control("Period", ["14 days", "30 days", "60 days"], default="30 days", key="path_span") or "30 days"
    n = int(span.split()[0])
    zoom = st.segmented_control("Map", ["Follow", "City", "Region"], default="Follow", key="path_zoom",
                                help="Follow: the map jumps to each day's trips as you move the slider.") or "Follow"
    trips = synth_trips(today)
    trips = trips[trips["start"] <= now].reset_index(drop=True)   # not cached: the clock moves
    days = list(pd.date_range(today - dt.timedelta(days=n - 1), today))
    in_span = trips[trips["start"] >= pd.Timestamp(days[0])].reset_index(drop=True)

    st.subheader("Path")
    st.plotly_chart(trail_figure(in_span, days, zoom), width="stretch", config={"displayModeBar": False})
    st.caption("Press Play or drag the day slider: the map jumps to that day's start and destination. Orange: that day. Dark grey: the 7 days before. Light grey: older. "
               "Lines are gentle curves between stops: the car does not report its route, only where it parks.")

    st.subheader("Timeline")
    st.plotly_chart(timeline_figure(trips, now), width="stretch", config={"displayModeBar": False})
    st.subheader("Places")
    st.dataframe(places_table(trips, now, n), hide_index=True, width="stretch")
