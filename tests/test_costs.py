import pandas as pd
import pytest

from polestar import config, costs, derive
from polestar.collect import connect

CH = "CHARGING_STATUS_V2_CHARGING"
IDLE = "CHARGING_STATUS_V2_IDLE"


@pytest.fixture
def db(tmp_path):
    return connect(str(tmp_path / "t.db"))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "none.toml"))
    return {**config.load(), "default_price": 0.25}


def battery():
    t = pd.to_datetime(["2026-01-01 10:00", "2026-01-01 10:30", "2026-01-01 11:00", "2026-01-01 12:00"], utc=True)
    return pd.DataFrame({"time": t, "batteryChargeLevelPercentage": [50, 55, 60, 60],
                         "chargingStatusV2": [IDLE, CH, CH, IDLE], "chargingPowerWatts": [0, 10000, 10000, 0],
                         "chargingType": ["CHARGING_TYPE_NONE", "CHARGING_TYPE_AC", "CHARGING_TYPE_AC", "CHARGING_TYPE_NONE"]})


def test_set_replace_and_clear_roundtrip(db):
    costs.set_cost(db, 1000, 4.5)
    costs.set_cost(db, 2000, 9)
    costs.set_cost(db, 1000, 5.25)                       # a second save replaces the first
    assert costs.load(db) == {1000: 5.25, 2000: 9.0}
    costs.clear(db, 1000)
    assert costs.load(db) == {2000: 9.0}


@pytest.mark.parametrize("bad", [-1, float("nan"), "abc"])
def test_invalid_costs_are_rejected(db, bad):
    with pytest.raises(ValueError):
        costs.set_cost(db, 1000, bad)
    assert costs.load(db) == {}


def test_key_is_whole_epoch_seconds_utc():
    assert costs.key(pd.Timestamp("2026-01-01 10:30:00.9", tz="UTC")) == 1767263400


def test_manual_cost_replaces_the_estimate(cfg):
    b = battery()
    s = derive.charge_sessions(b)
    est = derive.price_sessions(s, pd.DataFrame(), cfg, b)
    assert not est.loc[0, "manual"] and est.loc[0, "cost"] == pytest.approx(est.loc[0, "kwh_from_power"] * 0.25)
    out = derive.price_sessions(s, pd.DataFrame(), cfg, b, manual={costs.key(s.loc[0, "start"]): 3.0})
    assert out.loc[0, "manual"] and out.loc[0, "cost"] == 3.0
    assert out.loc[0, "cost_cheapest"] == 3.0 and out.loc[0, "saving"] == 0 and out.loc[0, "fee"] == 0
    assert out.loc[0, "price_per_kwh"] == pytest.approx(3.0 / out.loc[0, "kwh_from_power"])


def test_corrections_for_other_sessions_change_nothing(cfg):
    b = battery()
    s = derive.charge_sessions(b)
    out = derive.price_sessions(s, pd.DataFrame(), cfg, b, manual={12345: 99.0})
    assert not out["manual"].any() and out.loc[0, "cost"] != 99.0


def test_a_manual_cost_works_for_an_unpriced_place(cfg):
    b = battery()
    s = derive.charge_sessions(b)
    nocfg = {**cfg, "default_price": None}                # estimate unknown (NaN) until the owner enters a cost
    assert pd.isna(derive.price_sessions(s, pd.DataFrame(), nocfg, b).loc[0, "cost"])
    assert derive.price_sessions(s, pd.DataFrame(), nocfg, b, manual={costs.key(s.loc[0, "start"]): 7.5}).loc[0, "cost"] == 7.5


def test_location_set_replace_and_clear_roundtrip(db):
    costs.set_location(db, 1000, "  Ionity   A7 ")                 # spaces are tidied
    costs.set_location(db, 2000, "Work")
    costs.set_location(db, 2000, "Home")                           # a second save replaces the first
    assert costs.load_locations(db) == {1000: "Ionity A7", 2000: "Home"}
    costs.clear_location(db, 1000)
    assert costs.load_locations(db) == {2000: "Home"}


@pytest.mark.parametrize("bad", ["", "   ", "x" * 41])
def test_invalid_locations_are_rejected(db, bad):
    with pytest.raises(ValueError):
        costs.set_location(db, 1000, bad)
    assert costs.load_locations(db) == {}


def test_a_configured_location_reprices_the_session_and_a_free_name_is_only_a_label(cfg):
    b = battery()
    s = derive.charge_sessions(b)
    k = costs.key(s.loc[0, "start"])
    work = {"name": "Work", "lat": 0.0, "lon": 0.0, "radius_m": 100, "price_per_kwh": 0.10, "session_fee": 1.0}
    c = {**cfg, "locations": [work]}
    out = derive.price_sessions(s, pd.DataFrame(), c, b, locations={k: "Work"})
    assert out.loc[0, "location"] == "Work" and out.loc[0, "location_manual"]
    assert out.loc[0, "cost"] == pytest.approx(out.loc[0, "kwh_from_power"] * 0.10 + 1.0) and out.loc[0, "fee"] == 1.0
    out = derive.price_sessions(s, pd.DataFrame(), c, b, locations={k: "Ionity A7"})
    assert out.loc[0, "location"] == "Ionity A7" and out.loc[0, "fee"] == 0
    assert out.loc[0, "cost"] == pytest.approx(out.loc[0, "kwh_from_power"] * 0.25)     # the default price
    out = derive.price_sessions(s, pd.DataFrame(), c, b, manual={k: 3.0}, locations={k: "Work"})
    assert out.loc[0, "cost"] == 3.0 and out.loc[0, "location"] == "Work"               # an entered cost still wins
    assert not derive.price_sessions(s, pd.DataFrame(), c, b).loc[0, "location_manual"]
