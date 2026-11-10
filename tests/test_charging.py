import datetime as dt

import pandas as pd

from polestar import charging, config, derive

CET = dt.timezone(dt.timedelta(hours=1))
def at(day, hour, minute=0):   # November 2026: Mon 9, Tue 10, Wed 11, Thu 12, Fri 13, Sat 14, Sun 15
    return dt.datetime(2026, 11, day, hour, minute, tzinfo=CET)


def test_amp_check_and_next_timer_start():
    assert charging.amp_check(32, 20) == (32.0, 20.0)
    assert charging.amp_check(32, 30) is None and charging.amp_check(None, 20) is None
    timer = {"globalChargeTimer": {"activated": True, "start": {"hour": 23, "timeZone": {"offsetMinutes": 60}}}}
    assert charging.next_timer_start(timer, at(11, 21)) == at(11, 23)
    assert charging.next_timer_start({"globalChargeTimer": {"activated": False}}, at(11, 21)) is None


def test_price_at_windows_and_toml_tariffs(tmp_path):
    loc = {"price_per_kwh": 0.25, "tariff": [{"from": "23:00", "to": "07:00", "price_per_kwh": 0.15}]}
    assert config.price_at(loc, dt.datetime(2026, 1, 1, 23, 30)) == 0.15      # crosses midnight
    assert config.price_at(loc, dt.datetime(2026, 1, 1, 3, 0)) == 0.15
    assert config.price_at(loc, dt.datetime(2026, 1, 1, 12, 0)) == 0.25
    assert config.price_at({"price_per_kwh": 0.3}, dt.datetime(2026, 1, 1, 3, 0)) == 0.3
    p = tmp_path / "c.toml"
    p.write_text('timezone = "UTC"\n[[location]]\nname = "H"\nlat = 1\nlon = 2\nprice_per_kwh = 0.25\n'
                 '[[location.tariff]]\nfrom = "23:00"\nto = "07:00"\nprice_per_kwh = 0.15\n')
    cfg = config.load(p)
    assert cfg["timezone"] == "UTC" and cfg["locations"][0]["tariff"][0]["price_per_kwh"] == 0.15


def test_session_priced_per_interval_with_tariff():
    cfg = {"timezone": "Europe/Paris", "default_price": None, "locations": [
        {"name": "Home", "lat": 50.0, "lon": 3.0, "radius_m": 100, "price_per_kwh": 0.25,
         "tariff": [{"from": "23:00", "to": "07:00", "price_per_kwh": 0.15}]}]}
    # steady 6 kW from 22:00 to 02:00 Paris time (CET = UTC+1 in January) = 24 kWh: 6 kWh at 0.25, 18 kWh at 0.15
    times = pd.date_range("2026-01-01 21:00", periods=9, freq="30min", tz="UTC")
    battery = pd.DataFrame({"time": times, "chargingPowerWatts": [6000] * 9})
    sessions = pd.DataFrame({"start": [times[0]], "end": [times[-1]], "kwh_from_power": [24.0]})
    loc = pd.DataFrame({"time": [times[0] - pd.Timedelta("1h")], "coordinate.latitude": [50.0], "coordinate.longitude": [3.0]})
    out = derive.price_sessions(sessions, loc, cfg, battery)
    assert abs(out.loc[0, "cost"] - (6 * 0.25 + 18 * 0.15)) < 0.01
    assert abs(out.loc[0, "price_per_kwh"] - 4.2 / 24) < 0.001
    flat = derive.price_sessions(sessions, loc, cfg)           # no battery -> base price
    assert abs(flat.loc[0, "cost"] - 24 * 0.25) < 1e-9


def test_plug_issue_only_reports_exceptions():
    ok = {"chargerConnectionStatus": "CHARGER_CONNECTION_STATUS_CONNECTED", "batteryChargeLevelPercentage": 70}
    timer = at(11, 23)
    charging_now = {**ok, "chargingStatusV2": "CHARGING_STATUS_V2_CHARGING"}
    assert charging.plug_issue(charging_now, 90, None) is None
    assert charging.plug_issue({**ok, "chargingStatusV2": "CHARGING_STATUS_V2_DONE"}, 90, None) is None
    idle = {**ok, "chargingStatusV2": "CHARGING_STATUS_V2_IDLE"}
    assert charging.plug_issue(idle, 90, None)[0] == "warn"                    # below target, nothing scheduled
    assert charging.plug_issue(idle, 90, timer)[0] == "info"                   # a timer explains the wait
    assert charging.plug_issue({**idle, "batteryChargeLevelPercentage": 90}, 90, None) is None
    assert charging.plug_issue({**ok, "chargingStatusV2": "CHARGING_STATUS_V2_SCHEDULED"}, 90, None)[0] == "info"
    assert charging.plug_issue({**ok, "chargingStatusV2": "CHARGING_STATUS_V2_ERROR"}, 90, None)[0] == "error"
    assert charging.plug_issue({"chargerConnectionStatus": "CHARGER_CONNECTION_STATUS_DISCONNECTED"}, 90, None) is None


def test_cheapest_price_and_next_cheaper_start():
    loc = {"price_per_kwh": 0.25, "tariff": [{"from": "23:00", "to": "07:00", "price_per_kwh": 0.15}]}
    assert config.cheapest_price(loc) == 0.15 and config.cheapest_price({"price_per_kwh": 0.3}) == 0.3
    noon = dt.datetime(2026, 11, 11, 12, 0)
    assert config.next_cheaper_start(loc, noon) == dt.datetime(2026, 11, 11, 23, 0)
    assert config.next_cheaper_start(loc, dt.datetime(2026, 11, 11, 23, 30)) is None        # already cheapest
    assert config.next_cheaper_start({"price_per_kwh": 0.3}, noon) is None


def test_saving_is_peak_cost_minus_cheapest_cost():
    cfg = {"timezone": "Europe/Paris", "default_price": None, "locations": [
        {"name": "Home", "lat": 50.0, "lon": 3.0, "radius_m": 100, "price_per_kwh": 0.25,
         "tariff": [{"from": "23:00", "to": "07:00", "price_per_kwh": 0.15}]}]}
    times = pd.date_range("2026-01-01 21:00", periods=9, freq="30min", tz="UTC")   # 22:00-02:00 Paris
    battery = pd.DataFrame({"time": times, "chargingPowerWatts": [6000] * 9})
    sessions = pd.DataFrame({"start": [times[0]], "end": [times[-1]], "kwh_from_power": [24.0]})
    loc = pd.DataFrame({"time": [times[0] - pd.Timedelta("1h")], "coordinate.latitude": [50.0], "coordinate.longitude": [3.0]})
    out = derive.price_sessions(sessions, loc, cfg, battery)
    assert abs(out.loc[0, "saving"] - 6 * (0.25 - 0.15)) < 0.01     # only the 6 kWh before 23:00 were at peak
    flat = derive.price_sessions(sessions, loc, {**cfg, "locations": [{**cfg["locations"][0], "tariff": []}]}, battery)
    assert flat.loc[0, "saving"] == 0.0


def test_session_median_amps():
    ch = "CHARGING_STATUS_V2_CHARGING"
    b = pd.DataFrame({"time": pd.date_range("2026-01-01", periods=3, freq="10min", tz="UTC"),
                      "chargingStatusV2": [ch] * 3, "batteryChargeLevelPercentage": [50, 51, 52],
                      "chargingPowerWatts": [4400] * 3, "chargingCurrentAmps": [20, 20, 16]})
    assert derive.charge_sessions(b).loc[0, "median_amps"] == 20


HOME = {"price_per_kwh": 0.2142, "tariff": [{"from": "00:00", "to": "08:00", "price_per_kwh": 0.1589}]}
WEEKEND = {"price_per_kwh": 0.225, "tariff": [{"from": "00:00", "to": "00:00", "days": ["sat", "sun"], "price_per_kwh": 0.169}]}
TUE_NOON, SAT_NOON = dt.datetime(2026, 11, 10, 12, 0), dt.datetime(2026, 11, 14, 12, 0)   # a Tuesday, a Saturday


def test_home_overnight_window_00_to_08():
    assert config.price_at(HOME, dt.datetime(2026, 11, 10, 7, 59)) == 0.1589
    assert config.price_at(HOME, dt.datetime(2026, 11, 10, 8, 0)) == 0.2142
    assert config.price_at(HOME, dt.datetime(2026, 11, 10, 23, 59)) == 0.2142
    assert config.price_at(HOME, dt.datetime(2026, 11, 11, 0, 0)) == 0.1589


def test_weekend_only_tariff():
    assert config.price_at(WEEKEND, TUE_NOON) == 0.225 and config.price_at(WEEKEND, SAT_NOON) == 0.169
    assert config.price_at(WEEKEND, dt.datetime(2026, 11, 15, 23, 0)) == 0.169            # Sunday
    # a weekday charge cannot be moved to the weekend, so no cheaper alternative on weekdays
    assert config.cheapest_price(WEEKEND, TUE_NOON) == 0.225 and config.cheapest_price(WEEKEND, SAT_NOON) == 0.169
    assert config.next_cheaper_start(WEEKEND, TUE_NOON) is None                           # Saturday is > 24 h away
    friday_evening = dt.datetime(2026, 11, 13, 20, 0)
    assert config.next_cheaper_start(WEEKEND, friday_evening) == dt.datetime(2026, 11, 14, 0, 0)


def test_home_next_cheaper_start_is_midnight():
    assert config.next_cheaper_start(HOME, TUE_NOON) == dt.datetime(2026, 11, 11, 0, 0)
    assert config.next_cheaper_start(HOME, dt.datetime(2026, 11, 10, 3, 0)) is None


def test_no_saving_for_weekday_sessions_at_weekend_tariff_location():
    cfg = {"timezone": "Europe/Paris", "default_price": None,
           "locations": [{"name": "H2", "lat": 50.0, "lon": 3.0, "radius_m": 100, **WEEKEND}]}
    times = pd.date_range("2026-11-10 10:00", periods=5, freq="30min", tz="UTC")       # Tuesday
    battery = pd.DataFrame({"time": times, "chargingPowerWatts": [6000] * 5})
    sessions = pd.DataFrame({"start": [times[0]], "end": [times[-1]], "kwh_from_power": [12.0]})
    loc = pd.DataFrame({"time": [times[0] - pd.Timedelta("1h")], "coordinate.latitude": [50.0], "coordinate.longitude": [3.0]})
    out = derive.price_sessions(sessions, loc, cfg, battery)
    assert abs(out.loc[0, "cost"] - 12 * 0.225) < 0.01 and abs(out.loc[0, "saving"]) < 1e-9


def test_session_fee_is_added_once_and_does_not_change_saving():
    cfg = {"timezone": "Europe/Paris", "default_price": None, "locations": [
        {"name": "Work", "lat": 50.0, "lon": 3.0, "radius_m": 100, "price_per_kwh": 0.25, "session_fee": 6.0,
         "tariff": [{"from": "23:00", "to": "07:00", "price_per_kwh": 0.15}]}]}
    times = pd.date_range("2026-01-01 21:00", periods=9, freq="30min", tz="UTC")           # 22:00-02:00 Paris
    battery = pd.DataFrame({"time": times, "chargingPowerWatts": [6000] * 9})
    sessions = pd.DataFrame({"start": [times[0]], "end": [times[-1]], "kwh_from_power": [24.0]})
    loc = pd.DataFrame({"time": [times[0] - pd.Timedelta("1h")], "coordinate.latitude": [50.0], "coordinate.longitude": [3.0]})
    out = derive.price_sessions(sessions, loc, cfg, battery)
    assert abs(out.loc[0, "cost"] - (6 * 0.25 + 18 * 0.15 + 6.0)) < 0.01
    assert abs(out.loc[0, "saving"] - 6 * (0.25 - 0.15)) < 0.01
    assert out.loc[0, "fee"] == 6.0 and abs(out.loc[0, "price_per_kwh"] - 4.2 / 24) < 0.001   # energy price only
