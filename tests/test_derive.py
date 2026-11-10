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
