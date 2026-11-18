"""The Hero's Path: a map trail you can scrub or replay day by day, a stays timeline and a places table.

The car reports a position only when parked, never while driving, so a trip has no route, only where it
started and where it parked. The trail is therefore gentle curves between stops, never the actual road.

Places are the parked positions, named after the locations in config.toml when they fall inside one, and
otherwise grouped into "Place 1", "Place 2" and so on."""
import math
from contextlib import closing

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from . import admin, place_labels, theme
from .collect import connect
from .views import empty

CLUSTER_M = 150          # parked positions closer than this (and outside any configured location) are one place
MAX_DAYS = 60            # the day scrubber holds one Plotly frame per day, so keep it light
SHORT_HISTORY_DAYS = 14


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def seed_key(lat, lon):
    return f"{lat:.4f},{lon:.4f}"


def label_positions(location, cfg, saved=None):
    """Name every parked position. Returns (positions, places, seeds): `positions` has time, lat, lon and place per
    reading, `places` maps a place name to its (lat, lon), `seeds` maps the name of each grouped place to its seed key
    (what a saved label is stored under). A configured location wins; the rest are grouped greedily within CLUSTER_M
    and numbered by how often the car was seen there, unless `saved` ({seed key: label}) names them."""
    saved = saved or {}
    cols = ["time", "lat", "lon", "place"]
    if location.empty or "coordinate.latitude" not in location:
        return pd.DataFrame(columns=cols), {}, {}
    pos = location.dropna(subset=["coordinate.latitude", "coordinate.longitude"]).sort_values("time")
    pos = pos.rename(columns={"coordinate.latitude": "lat", "coordinate.longitude": "lon"})[["time", "lat", "lon"]]
    named = cfg.get("locations", [])
    clusters, labels = [], []           # cluster: [lat_sum, lon_sum, count]
    for r in pos.itertuples(index=False):
        near = min(named, key=lambda x: haversine_m(r.lat, r.lon, x["lat"], x["lon"]), default=None)
        if near is not None and haversine_m(r.lat, r.lon, near["lat"], near["lon"]) <= near.get("radius_m", 100):
            labels.append(("named", near["name"]))
            continue
        for i, c in enumerate(clusters):
            if haversine_m(r.lat, r.lon, c[0] / c[2], c[1] / c[2]) <= CLUSTER_M:
                c[0] += r.lat; c[1] += r.lon; c[2] += 1
                labels.append(("cluster", i))
                break
        else:
            clusters.append([r.lat, r.lon, 1, seed_key(r.lat, r.lon)])
            labels.append(("cluster", len(clusters) - 1))
    order = sorted(range(len(clusters)), key=lambda i: -clusters[i][2])
    used = {x["name"] for x in named}
    names = {}
    for n, i in enumerate(order):
        base = saved.get(clusters[i][3]) or f"Place {n + 1}"
        name, k = base, 2
        while name in used:                                        # a label never merges two places
            name, k = f"{base} ({k})", k + 1
        used.add(name)
        names[i] = name
    pos = pos.assign(place=[v if kind == "named" else names[v] for kind, v in labels]).reset_index(drop=True)
    places = {x["name"]: (x["lat"], x["lon"]) for x in named if x["name"] in set(pos["place"])}
    places.update({names[i]: (c[0] / c[2], c[1] / c[2]) for i, c in enumerate(clusters)})
    seeds = {names[i]: c[3] for i, c in enumerate(clusters)}
    return pos, places, seeds


def stops(trips, positions):
    """Add where each trip started and ended: `frm` is the place of the last position at or before the start
    (or where the previous trip ended), `to` the first position after the end and before the next trip starts.
    None when unknown (the car did not report a position)."""
    out = trips[["start", "end", "km"]].sort_values("start").reset_index(drop=True)
    frm, to = [], []
    times = positions["time"].tolist()
    places = positions["place"].tolist()
    for i, t in out.iterrows():
        before = [p for tm, p in zip(times, places) if tm <= t["start"]]
        nxt = out["start"].iloc[i + 1] if i + 1 < len(out) else None
        after = [p for tm, p in zip(times, places) if tm > t["end"] and (nxt is None or tm < nxt)]
        frm.append(before[-1] if before else (to[-1] if to else None))
        to.append(after[0] if after else None)
    return out.assign(frm=frm, to=to)


def drawable(trips):
    """Trips whose two ends are known, distinct places: the only ones that can be drawn as a curve."""
    return trips[trips["frm"].notna() & trips["to"].notna() & (trips["frm"] != trips["to"])]


def curve(places, a, b, n=14):
    """A gentle arc from place a to place b (the real road is unknown, so this is only a guess)."""
    (la1, lo1), (la2, lo2) = places[a], places[b]
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


def lines(places, trips):
    lat, lon = [], []
    for _, t in drawable(trips).iterrows():
        la, lo = curve(places, t["frm"], t["to"])
        lat += la + [None]; lon += lo + [None]
    return lat, lon


def stays(trips, window_start, now):
    """Rows for the stays timeline: (start, end, row, kind)."""
    first = trips["frm"].dropna()
    here = first.iloc[0] if len(first) else "Unknown"          # where the car stood before its first trip in view
    rows, since = [], pd.Timestamp(window_start)
    for _, t in trips.iterrows():
        rows.append((since, t["start"], here, "Stay"))
        rows.append((t["start"], t["end"], "Driving", "Driving"))
        here, since = (t["to"] if pd.notna(t["to"]) else "Unknown"), t["end"]
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


def trail_figure(places, trips, days, zoom="Follow"):
    p = theme.palette()
    dark = theme.is_dark()
    visits = trips["to"].value_counts()
    names = list(places)
    fig = go.Figure()
    fig.add_trace(go.Scattermap(
        lat=[places[n][0] for n in names], lon=[places[n][1] for n in names], mode="markers+text", text=names,
        textposition="top right", textfont=dict(size=12, color=p["ink"]),
        marker=dict(size=[9 + 3 * math.sqrt(visits.get(n, 0)) for n in names], color=p["muted"]),
        hovertext=[f"{n}: {visits.get(n, 0)} arrivals in this period" for n in names], hoverinfo="text", name="places"))
    fallback = places[names[0]]
    centre = (sum(v[0] for v in places.values()) / len(places), sum(v[1] for v in places.values()) / len(places))

    def day_traces(i):
        d = days[i]
        day = trips["start"].dt.normalize()
        old = trips[day < d - pd.DateOffset(days=6)]
        recent = trips[(day >= d - pd.DateOffset(days=6)) & (day < d)]
        today = trips[day == d]
        known = trips[(day <= d) & trips["to"].notna()]
        here = known["to"].iloc[-1] if len(known) else names[0]
        old_l, recent_l, today_l = lines(places, old), lines(places, recent), lines(places, today)
        # the camera jumps to where the day happened: origin, destination and everything in between
        if any(v is not None for v in today_l[0]):
            view = fit_view(*today_l)
        else:
            view = dict(lat=places[here][0], lon=places[here][1]), 10.5
        return [
            go.Scattermap(lat=old_l[0], lon=old_l[1], mode="lines", line=dict(width=2, color=p["faint"]), name="older", hoverinfo="skip"),
            go.Scattermap(lat=recent_l[0], lon=recent_l[1], mode="lines", line=dict(width=3, color=p["baseline"]), name="last 7 days", hoverinfo="skip"),
            go.Scattermap(lat=today_l[0], lon=today_l[1], mode="lines", line=dict(width=5, color=p["series"]), name="that day", hoverinfo="skip"),
            go.Scattermap(lat=[places[here][0]], lon=[places[here][1]], mode="markers", marker=dict(size=19, color=p["series"]), name="car", hoverinfo="skip"),
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
    all_view = fit_view([v[0] for v in places.values()], [v[1] for v in places.values()]) if len(places) > 1 else (dict(lat=fallback[0], lon=fallback[1]), 12)
    fig.update_layout(
        map=dict(style="carto-darkmatter" if dark else "carto-positron",
                 center=last_view[0] if zoom == "Follow" else dict(lat=centre[0], lon=centre[1]) if zoom == "City" else all_view[0],
                 zoom=last_view[1] if zoom == "Follow" else 11 if zoom == "City" else all_view[1]),
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


def window_start(now, days, first_seen=None):
    """Start of the last `days` calendar days, never before the first data (no stay is counted before tracking began)."""
    start = (pd.Timestamp(now) - pd.DateOffset(days=days)).normalize()
    return start if first_seen is None else max(start, pd.Timestamp(first_seen).tz_convert(start.tz))


def timeline_figure(trips, now, days=7, first_seen=None):
    p = theme.palette()
    start = window_start(now, days, first_seen)
    df = stays(trips[trips["end"] >= start], start, pd.Timestamp(now))
    order = ["Driving"] + sorted(set(df["row"]) - {"Driving"})
    fig = px.timeline(df, x_start="start", x_end="end", y="row", color="kind", category_orders={"row": order},
                      color_discrete_map={"Stay": p["baseline"], "Driving": p["series"]})
    fig.update_yaxes(title=None, autorange="reversed")
    fig.update_xaxes(title=None)
    return theme.style(fig, 300, f"Where the car was, last {days} days").update_layout(showlegend=False)


def places_table(trips, now, days, first_seen=None):
    start = window_start(now, days, first_seen)
    df = stays(trips[trips["end"] >= start], start, pd.Timestamp(now))
    df = df[df["kind"] == "Stay"].assign(hours=lambda d: (d["end"] - d["start"]).dt.total_seconds() / 3600)
    arrivals = trips[trips["end"] >= start].groupby("to").agg(arrivals=("km", "size"), km_in=("km", "sum"))
    out = df.groupby("row").agg(hours=("hours", "sum")).join(arrivals, how="outer").fillna(0).reset_index(names="place")
    out["share"] = out["hours"] / max(out["hours"].sum(), 1) * 100
    return out.sort_values("hours", ascending=False).assign(
        hours=lambda d: d["hours"].round(0), km_in=lambda d: d["km_in"].round(0), share=lambda d: d["share"].round(0)
    ).rename(columns={"hours": "time there (h)", "km_in": "km driven to", "share": "% of time"})


def day_range(first, today, n):
    """Local midnights of the last `n` calendar days up to `today`, not before `first`. Calendar offsets, not 24 h
    timedeltas: across a daylight-saving change a Timedelta lands at 23:00 the day before and drops today."""
    return list(pd.date_range(max(today - pd.DateOffset(days=n - 1), first), today, freq="D"))


def edit_labels(seeds, saved, arrivals, db_path):
    """Name a grouped place, behind the admin password (the same session unlock as the manual charge costs)."""
    if not seeds:
        st.caption("Every place already has a name from config.toml.")
    elif not admin.configured():
        st.caption(":material/lock: Editing is off. Set `ADMIN_PASSWORD` in `.env` and restart to enable it.")
    elif not admin.unlocked(st.session_state):
        with st.form("admin_unlock_path", clear_on_submit=True, border=False):
            pw = st.text_input("Admin password", type="password", key="admin_pw_path")
            if st.form_submit_button("Unlock"):
                problem = admin.unlock(st.session_state, pw)
                if problem:
                    st.error(problem)
                else:
                    st.rerun()
    else:
        name = st.selectbox("Place", list(seeds), key="path_label_pick",
                            format_func=lambda n: f"{n} ({int(arrivals.get(n, 0))} arrivals)")
        seed = seeds[name]
        c1, c2, c3 = st.columns([2, 1, 1], vertical_alignment="bottom")
        text = c1.text_input("Label", value=saved.get(seed, ""), key=f"path_label_{seed}", max_chars=place_labels.MAX_LEN,
                             help="A name for this place, such as Gym or Parents. It replaces the automatic name here.")
        if c2.button("Save label", key="path_label_save", width="stretch") and admin.unlocked(st.session_state):
            try:
                with closing(connect(db_path)) as wdb:
                    place_labels.set_label(wdb, seed, text)
            except ValueError as e:
                st.error(str(e))
            else:
                st.rerun()
        if c3.button("Remove label", key="path_label_clear", width="stretch", disabled=seed not in saved) and admin.unlocked(st.session_state):
            with closing(connect(db_path)) as wdb:
                place_labels.clear(wdb, seed)
            st.session_state.pop(f"path_label_{seed}", None)
            st.rerun()
        if st.button("Lock", key="admin_lock_path", type="tertiary"):
            admin.lock(st.session_state)
            st.rerun()


def render(trip_table, location, cfg, period_days, tz, saved=None, db_path=None):
    """The Path tab. `trip_table` is derive.trips() (times in the display zone), `location` the position snapshots,
    `saved` the place labels ({seed key: label}) and `db_path` where a new label is written."""
    saved = saved or {}
    positions, places, seeds = label_positions(location, cfg, saved)
    if not places or trip_table.empty:
        empty("No path yet: a stop appears once the car has driven and then parked with a reported position.",
              ":material/route:")
        return
    now = pd.Timestamp.now(tz=tz)
    trips = stops(trip_table, positions)
    trips = trips[trips["start"] <= now].reset_index(drop=True)
    today = now.normalize()
    first = trips["start"].min().normalize()
    first_seen = min(positions["time"].min(), trips["start"].min())
    n = min(period_days or MAX_DAYS, MAX_DAYS)
    days = day_range(first, today, n)
    span = (today - first).days + 1
    if span < SHORT_HISTORY_DAYS:
        st.caption(f":material/info: History is short ({span} days): the path fills in as data builds up.")
    zoom = st.segmented_control("Map", ["Follow", "City", "Region"], default="Follow", key="path_zoom",
                                help="Follow: the map jumps to each day's trips as you move the slider.") or "Follow"
    in_span = trips[trips["start"] >= days[0]].reset_index(drop=True)
    unlinked = len(in_span) - len(drawable(in_span))

    st.subheader("Path")
    st.plotly_chart(trail_figure(places, in_span, days, zoom), width="stretch", config={"displayModeBar": False})
    st.caption("Press Play or drag the day slider: the map jumps to that day's start and destination. Orange: that day. "
               "Grey: the 7 days before. Faint grey: older. Lines are gentle curves between stops: the car does not "
               "report its route, only where it parks."
               + (f" {unlinked} of {len(in_span)} trips have no drawable line (no position reported at one end, or a round trip)." if unlinked else ""))

    st.subheader("Timeline")
    st.plotly_chart(timeline_figure(trips, now, first_seen=first_seen), width="stretch", config={"displayModeBar": False})
    st.subheader("Places")
    st.dataframe(places_table(trips, now, n, first_seen), hide_index=True, width="stretch")
    with st.expander("Name a place"):
        edit_labels(seeds, saved, trips["to"].value_counts(), db_path)
