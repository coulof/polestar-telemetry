import pandas as pd

from polestar import derive


def ts(*minutes):
    return pd.to_datetime(["2026-01-01 08:00"], utc=True).repeat(len(minutes)) + pd.to_timedelta(minutes, unit="min")


def odo(minutes, meters):
    return pd.DataFrame({"time": ts(*minutes), "odometerMeters": meters})


def bat(minutes, soc, status=None, watts=None):
    n = len(minutes)
    return pd.DataFrame({
        "time": ts(*minutes),
        "batteryChargeLevelPercentage": soc,
        "chargingStatusV2": status or ["CHARGING_STATUS_V2_IDLE"] * n,
        "chargingPowerWatts": watts or [0] * n,
        "chargingType": ["CHARGING_TYPE_AC" if w else "CHARGING_TYPE_NONE" for w in (watts or [0] * n)],
    })


def test_trips_split_on_gap_and_compute_distance_and_energy(monkeypatch):
    monkeypatch.setattr(derive, "BATTERY_KWH", 80)
    # parked 100km; drives 10 km over 20 min; long gap; drives 5 km
    o = odo([0, 5, 10, 20, 200, 210], [100_000, 105_000, 108_000, 110_000, 110_000, 115_000])
    b = bat([0, 25, 205, 215], [80, 78, 78, 77])
    t = derive.trips(o, b)
    assert len(t) == 2
    assert t.loc[0, "km"] == 10 and t.loc[0, "minutes"] == 20
    assert t.loc[0, "avg_kmh"] == 30
    assert t.loc[0, "kwh"] == (80 - 78) / 100 * 80
    assert t.loc[1, "km"] == 5


def test_trips_empty():
    assert derive.trips(pd.DataFrame(), pd.DataFrame()).empty


def test_charge_session_energy_and_peak():
    b = bat(
        [0, 60, 120, 180, 600],
        [50, 50, 60, 70, 70],
        ["CHARGING_STATUS_V2_IDLE", "CHARGING_STATUS_V2_CHARGING", "CHARGING_STATUS_V2_CHARGING",
         "CHARGING_STATUS_V2_CHARGING", "CHARGING_STATUS_V2_DONE"],
        [0, 7000, 7000, 3000, 0],
    )
    s = derive.charge_sessions(b)
    assert len(s) == 1
    r = s.iloc[0]
    assert r["peak_kw"] == 7
    assert r["soc_start"] == 50 and r["soc_end"] == 70
    assert r["type"] == "AC"
    assert r["minutes"] == 120
    assert abs(r["kwh_from_power"] - (7 + 5)) < 1e-6  # trapezoid: 60-120 @7kW, 120-180 avg(7,3)


def test_two_sessions_split_by_idle():
    st = ["CHARGING_STATUS_V2_CHARGING", "CHARGING_STATUS_V2_IDLE", "CHARGING_STATUS_V2_CHARGING", "CHARGING_STATUS_V2_CHARGING"]
    s = derive.charge_sessions(bat([0, 30, 60, 90], [10, 12, 12, 15], st, [5000, 0, 5000, 5000]))
    assert len(s) == 2


def test_idle_drain_excludes_trips_and_charging():
    b = bat([0, 60, 120, 180], [80, 79, 75, 74])
    trip = pd.DataFrame({"start": ts(70), "end": ts(110)})
    d = derive.idle_drain(b, trip)
    # 80->79 parked (1); 79->75 overlaps the trip (ignored); 75->74 parked (1)
    assert d["soc_lost_pct"].tolist() == [2]


CFG = {
    "interval": 300, "interval_active": 60, "default_price": None,
    "locations": [
        {"name": "Home", "lat": 50.0, "lon": 3.0, "radius_m": 100, "price_per_kwh": 0.0},
        {"name": "Work", "lat": 50.1, "lon": 3.1, "radius_m": 150, "price_per_kwh": 0.2},
    ],
}


def test_price_sessions_matches_location_at_session_start():
    sessions = pd.DataFrame({"start": ts(100, 500, 900), "kwh_from_power": [10.0, 10.0, 10.0]})
    loc = pd.DataFrame({
        "time": ts(0, 400, 800),
        "coordinate.latitude": [50.0, 50.1, 51.0],
        "coordinate.longitude": [3.0, 3.1, 4.0],
    })
    out = derive.price_sessions(sessions, loc, CFG)
    assert out["location"].tolist()[:2] == ["Home", "Work"]
    assert pd.isna(out["location"].iloc[2])
    assert out["cost"].iloc[:2].tolist() == [0.0, 2.0]
    assert pd.isna(out["cost"].iloc[2])  # unknown place, no default price


def test_price_sessions_without_location_data():
    s = pd.DataFrame({"start": ts(0), "kwh_from_power": [5.0]})
    out = derive.price_sessions(s, pd.DataFrame(), {**CFG, "default_price": 0.3})
    assert out["cost"].iloc[0] == 1.5


def test_asof_accepts_local_timezone_times():
    loc = pd.DataFrame({"time": ts(0, 400), "coordinate.latitude": [1.0, 2.0], "coordinate.longitude": [1.0, 2.0]})
    loc["time"] = loc["time"].dt.tz_convert("Europe/Paris").astype("datetime64[us, Europe/Paris]")
    s = pd.DataFrame({"start": ts(500), "kwh_from_power": [1.0]})
    out = derive.price_sessions(s, loc, {**CFG, "locations": [{"name": "X", "lat": 2.0, "lon": 2.0, "radius_m": 50, "price_per_kwh": 1.0}]})
    assert out["location"].iloc[0] == "X"


def pre(minutes, aqi, pm, cycle_s=None):
    df = pd.DataFrame({
        "time": ts(*minutes), "measuredAirQualityIndex": aqi, "measuredParticulateMatter25": pm,
        "lastCycleValid": [True] * len(minutes),
    })
    if cycle_s is not None:
        df["lastCycleCompleted.seconds"] = [str(s) for s in cycle_s]
    return df


def test_air_quality_daily_and_cycles():
    p = pre([0, 60, 1500], [2, 4, 3], [2, 6, 5], cycle_s=[1_000_000, 1_000_000, 1_090_000])
    a = derive.air_quality(p)
    assert a["aqi"].tolist() == [2, 4, 3]
    d = derive.air_daily(a)
    assert d["readings"].tolist() == [2, 1]                  # two readings day 1, one day 2
    assert d.loc[0, "aqi_avg"] == 3 and d.loc[0, "pm25_max"] == 6
    c = derive.cleaning_cycles(a)
    assert len(c) == 2 and c.loc[0, "completed"] > c.loc[1, "completed"]


def test_air_quality_empty_and_missing_columns():
    assert derive.air_quality(pd.DataFrame()).empty
    assert derive.air_daily(derive.air_quality(pd.DataFrame())).empty
    assert derive.cleaning_cycles(derive.air_quality(pd.DataFrame())).empty


def test_session_started_before_tracking_is_partial():
    ch, idle = "CHARGING_STATUS_V2_CHARGING", "CHARGING_STATUS_V2_IDLE"
    first_is_charging = derive.charge_sessions(bat([0, 30], [50, 52], [ch, ch], [4000, 4000]))
    assert first_is_charging["partial"].tolist() == [True]
    observed_start = derive.charge_sessions(bat([0, 30, 60], [50, 50, 52], [idle, ch, ch], [0, 4000, 4000]))
    assert observed_start["partial"].tolist() == [False]
    after_gap = derive.charge_sessions(bat([0, 600], [50, 60], [idle, ch], [0, 4000]))  # 10 h of silence
    assert after_gap["partial"].tolist() == [True]


def avail(minutes, modes):
    return pd.DataFrame({"time": ts(*minutes), "usageMode": modes})


DRIVE, STOP = "USAGE_MODE_DRIVING", "USAGE_MODE_INACTIVE"


def test_trip_from_driving_window_with_start_and_end_odometer_only(monkeypatch):
    # What the car really sends: one odometer event at drive start, one at the end (22 min apart),
    # the end reading arriving just before the mode change.
    monkeypatch.setattr(derive, "BATTERY_KWH", 80)
    o = odo([0, 21.9], [6_210_700, 6_229_100])
    a = avail([0, 22], [DRIVE, STOP])
    b = bat([0, 25], [80, 78])
    t = derive.trips(o, b, availability=a)
    assert len(t) == 1
    assert abs(t.loc[0, "km"] - 18.4) < 1e-9 and t.loc[0, "minutes"] == 22
    assert abs(t.loc[0, "avg_kmh"] - 18.4 / 22 * 60) < 1e-9
    assert t.loc[0, "soc_start"] == 80 and t.loc[0, "soc_end"] == 78


def test_each_driving_window_is_its_own_trip_even_after_a_short_stop():
    o = odo([0, 21.9, 24, 39.9], [100_000, 118_400, 118_400, 130_000])
    t = derive.trips(o, bat([0], [80]), availability=avail([0, 22, 24, 40], [DRIVE, STOP, DRIVE, STOP]))
    assert len(t) == 2 and t["km"].round(1).tolist() == [18.4, 11.6] and t["minutes"].tolist() == [22, 16]


def test_avg_consumption_ignores_trips_without_energy_estimate():
    t = pd.DataFrame({"km": [10.0, 20.0, 5.0], "kwh": [1.5, 3.0, float("nan")]})
    assert abs(derive.avg_consumption(t) - 15.0) < 1e-9            # 4.5 kWh over the 30 km that have an estimate
    assert pd.isna(derive.avg_consumption(pd.DataFrame({"km": [5.0], "kwh": [float("nan")]})))
    assert pd.isna(derive.avg_consumption(pd.DataFrame(columns=["km", "kwh"])))


def test_open_window_and_stationary_window_make_no_trip():
    o = odo([0], [100_000])
    assert derive.trips(o, bat([0], [80]), availability=avail([0], [DRIVE])).empty         # still driving
    o2 = odo([0, 20], [100_000, 100_000])
    assert derive.trips(o2, bat([0], [80]), availability=avail([0, 20], [DRIVE, STOP])).empty   # did not move


def test_trips_fall_back_to_odometer_without_usage_mode():
    o = odo([0, 5, 10], [100_000, 105_000, 110_000])
    for a in (None, pd.DataFrame()):
        t = derive.trips(o, bat([0], [80]), availability=a)
        assert len(t) == 1 and t.loc[0, "km"] == 10.0


def test_trip_soc_end_uses_last_reading_when_none_follows():
    o = odo([0, 21.9], [100_000, 118_400])
    t = derive.trips(o, bat([0, 20], [80, 78]), availability=avail([0, 22], [DRIVE, STOP]))   # no battery event after 22 min
    assert t.loc[0, "soc_end"] == 78 and pd.notna(t.loc[0, "kwh"])


def test_end_odometer_never_reaches_into_the_next_drive():
    # real pattern: the second leg ends only 5 minutes after the first, inside the 10-minute tolerance
    o = odo([0, 21.9, 24, 29.9], [100_000, 118_400, 118_400, 119_100])
    t = derive.trips(o, bat([0, 40], [83, 79]), availability=avail([0, 22, 24, 30], [DRIVE, STOP, DRIVE, STOP]))
    assert t["km"].round(1).tolist() == [18.4, 0.7]


def test_very_short_trips_get_no_energy_estimate():
    o = odo([0, 5], [100_000, 100_700])
    t = derive.trips(o, bat([0, 10], [80, 80]), availability=avail([0, 5], [DRIVE, STOP]))
    assert len(t) == 1 and pd.isna(t.loc[0, "kwh"]) and pd.isna(t.loc[0, "kwh_per_100km"])
