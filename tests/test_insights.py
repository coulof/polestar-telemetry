import pandas as pd

from polestar import insights
from tests.test_derive import ts


def test_real_range():
    trips = pd.DataFrame({"km": [30.0, 10.0, 5.0], "soc_start": [80, 70, 60], "soc_end": [75, 68, 60]})
    r = insights.real_range(trips)   # third trip drops 0 %: ignored
    assert r["trips"] == 2
    assert abs(r["km_per_pct"] - 40 / 7) < 1e-9 and abs(r["range_100_km"] - 4000 / 7) < 1e-9
    assert insights.real_range(pd.DataFrame()) is None


def test_capacity_trend_filters_small_sessions():
    s = pd.DataFrame({"start": ts(0, 100), "soc_start": [50, 50], "soc_end": [60, 52], "kwh_from_power": [8.0, 1.5]})
    c = insights.capacity_trend(s)
    assert len(c) == 1 and c.loc[0, "capacity_kwh"] == 80.0


def test_monthly_cost_per_100km_and_co2():
    s = pd.DataFrame({"start": ts(0, 60), "kwh_from_power": [10.0, 10.0], "cost": [2.0, 3.0]})
    t = pd.DataFrame({"start": ts(30), "km": [100.0]})
    m = insights.monthly(s, t, g_per_kwh=50)
    assert m.loc[0, "kwh"] == 20 and m.loc[0, "cost"] == 5 and m.loc[0, "sessions"] == 2
    assert m.loc[0, "cost_per_100km"] == 5.0
    assert m.loc[0, "co2_kg"] == 1.0 and m.loc[0, "co2_g_per_km"] == 10.0


def test_monthly_unpriced_and_no_km():
    s = pd.DataFrame({"start": ts(0), "kwh_from_power": [10.0], "cost": [float("nan")]})
    m = insights.monthly(s, pd.DataFrame())
    assert pd.isna(m.loc[0, "cost"]) and pd.isna(m.loc[0, "cost_per_100km"])
    assert insights.monthly(pd.DataFrame(), pd.DataFrame()).empty


def test_state_segments_merge_and_charging():
    a = pd.DataFrame({"time": ts(0, 10, 20, 40), "usageMode": [
        "USAGE_MODE_INACTIVE", "USAGE_MODE_ABANDONED", "USAGE_MODE_DRIVING", "USAGE_MODE_INACTIVE"]})
    sess = pd.DataFrame({"start": ts(100), "end": ts(160)})
    seg = insights.state_segments(a, sess, ts(60)[0])
    by = seg.groupby("state").size().to_dict()
    assert by == {"Charging": 1, "Driving": 1, "Parked": 2}   # the two parked events merge, then the last one is separate
    parked = seg[seg.state == "Parked"].sort_values("start")
    assert (parked["end"].iloc[0] - parked["start"].iloc[0]) == pd.Timedelta("20min")


def test_activity_grid_splits_hours_and_midnight_and_skips_parked():
    t = lambda s: pd.Timestamp(s, tz="UTC")
    seg = pd.DataFrame({"start": [t("2026-05-11 21:50"), t("2026-05-12 08:00"), t("2026-05-12 10:10")],
                        "end": [t("2026-05-11 22:20"), t("2026-05-12 10:00"), t("2026-05-12 10:40")],
                        "state": ["Driving", "Parked", "Charging"]})
    g = insights.activity_grid(seg, "Europe/Paris")        # UTC+2 in May: 23:50-00:20 local, spans midnight
    rows = {(str(r.date), r.hour, r.state): r.minutes for r in g.itertuples()}
    assert rows == {("2026-05-11", 23, "Driving"): 10, ("2026-05-12", 0, "Driving"): 20, ("2026-05-12", 12, "Charging"): 30}


def test_activity_grid_keeps_the_state_with_more_minutes_and_handles_empty():
    t = lambda s: pd.Timestamp(s, tz="UTC")
    seg = pd.DataFrame({"start": [t("2026-05-12 10:00"), t("2026-05-12 10:40")],
                        "end": [t("2026-05-12 10:40"), t("2026-05-12 10:50")], "state": ["Charging", "Driving"]})
    assert insights.activity_grid(seg, "UTC")["state"].tolist() == ["Charging"]
    assert insights.activity_grid(seg.iloc[0:0], "UTC").empty
