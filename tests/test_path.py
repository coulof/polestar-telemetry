"""Hero's Path: place labelling and stop derivation on small synthetic frames (made-up coordinates)."""
import pandas as pd

from polestar import path

HOME, FAR = (57.7000, 11.9000), (57.8000, 12.1000)
CFG = {"locations": [{"name": "Home", "lat": HOME[0], "lon": HOME[1], "radius_m": 100}]}


def loc(rows):
    return pd.DataFrame({"time": pd.to_datetime([r[0] for r in rows], utc=True),
                         "coordinate.latitude": [r[1] for r in rows], "coordinate.longitude": [r[2] for r in rows]})


def test_configured_location_names_a_nearby_position_and_others_are_clustered():
    near_home = (HOME[0] + 0.0003, HOME[1])                     # ~33 m
    df = loc([("2026-11-10 08:00", *near_home), ("2026-11-10 09:00", *FAR),
              ("2026-11-11 09:00", FAR[0] + 0.0004, FAR[1]), ("2026-11-12 09:00", 58.5, 12.5)])
    pos, places = path.label_positions(df, CFG)
    assert list(pos["place"]) == ["Home", "Place 1", "Place 1", "Place 2"]     # the busiest cluster is Place 1
    assert set(places) == {"Home", "Place 1", "Place 2"}
    assert places["Home"] == HOME                                # configured coordinates win


def test_position_outside_the_radius_is_not_home():
    df = loc([("2026-11-10 08:00", HOME[0] + 0.002, HOME[1])])   # ~220 m
    pos, _ = path.label_positions(df, CFG)
    assert list(pos["place"]) == ["Place 1"]


def test_no_positions_gives_no_places():
    pos, places = path.label_positions(pd.DataFrame(), CFG)
    assert pos.empty and places == {}


def trips(rows):
    return pd.DataFrame({"start": pd.to_datetime([r[0] for r in rows], utc=True),
                         "end": pd.to_datetime([r[1] for r in rows], utc=True), "km": [r[2] for r in rows]})


def test_stops_use_the_position_before_the_start_and_after_the_end():
    df = loc([("2026-11-10 07:00", *HOME), ("2026-11-10 09:00", *FAR), ("2026-11-10 18:30", *HOME)])
    pos, _ = path.label_positions(df, CFG)
    t = path.stops(trips([("2026-11-10 08:00", "2026-11-10 08:30", 20.0), ("2026-11-10 18:00", "2026-11-10 18:20", 20.0)]), pos)
    assert list(t["frm"]) == ["Home", "Place 1"]
    assert list(t["to"]) == ["Place 1", "Home"]
    assert len(path.drawable(t)) == 2


def test_a_trip_without_a_later_position_is_kept_but_not_drawn():
    df = loc([("2026-11-10 07:00", *HOME)])
    pos, _ = path.label_positions(df, CFG)
    t = path.stops(trips([("2026-11-10 08:00", "2026-11-10 08:30", 20.0)]), pos)
    assert len(t) == 1 and t["to"].isna().all()
    assert path.drawable(t).empty


def test_the_destination_must_come_before_the_next_trip_starts():
    df = loc([("2026-11-10 07:00", *HOME), ("2026-11-10 12:00", *FAR)])
    pos, _ = path.label_positions(df, CFG)
    t = path.stops(trips([("2026-11-10 08:00", "2026-11-10 08:30", 5.0), ("2026-11-10 09:00", "2026-11-10 09:20", 5.0)]), pos)
    assert t["to"].isna().tolist() == [True, False]              # a late position belongs to the second trip's end
    assert list(t["frm"]) == ["Home", "Home"]


def test_a_round_trip_is_not_drawn_and_unknown_stays_do_not_break_the_timeline():
    df = loc([("2026-11-10 07:00", *HOME), ("2026-11-10 09:00", *HOME)])
    pos, _ = path.label_positions(df, CFG)
    t = path.stops(trips([("2026-11-10 08:00", "2026-11-10 08:30", 3.0)]), pos)
    assert path.drawable(t).empty
    rows = path.stays(t, pd.Timestamp("2026-11-10 06:00", tz="UTC"), pd.Timestamp("2026-11-10 12:00", tz="UTC"))
    assert set(rows["kind"]) == {"Stay", "Driving"}


def test_day_range_keeps_calendar_days_across_the_daylight_saving_change():
    today = pd.Timestamp("2026-11-17 13:00", tz="Europe/Stockholm").normalize()     # DST ended on 25 Oct
    first = pd.Timestamp("2026-10-01", tz="Europe/Stockholm")
    days = path.day_range(first, today, 30)
    assert days[-1] == today and len(days) == 30
    assert all(d.hour == 0 for d in days)                                           # no day shifted to 23:00
    assert path.day_range(today, today, 30) == [today]                              # not before the first trip
