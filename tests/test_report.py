import datetime as dt
import json
import stat
from zoneinfo import ZoneInfo

import pytest

from polestar import costs, report, stats
from polestar.collect import connect, store

VIN = "TESTVIN0123456789"
TZ = ZoneInfo("Europe/Paris")


@pytest.fixture(autouse=True)
def no_personal_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "none.toml"))   # defaults only, never the real config.toml


def epoch(day, hour, minute=0):
    return (dt.datetime(2026, 5, day, hour, tzinfo=TZ) + dt.timedelta(minutes=minute)).timestamp()


def snap(db, n, domain, t, **p):
    store(db, VIN, domain, {"vin": VIN, "timestamp": {"seconds": str(int(t))}, "metaEventId": f"e{n}", **p})


@pytest.fixture
def db(tmp_path):
    d = connect(str(tmp_path / "t.db"))
    n = 0
    # 2026-W20 = Mon 11 May .. Sun 17 May. One 30 km trip (6 % SoC) on Tue 12 May, one charge on Wed 13 May.
    for k in range(7):
        n += 1; snap(d, n, "odometer", epoch(12, 7, 5 * k), odometerMeters=5_000_000 + 5_000 * k)
    n += 1; snap(d, n, "availability", epoch(12, 7), usageMode="USAGE_MODE_DRIVING")
    n += 1; snap(d, n, "availability", epoch(12, 7, 30), usageMode="USAGE_MODE_INACTIVE")
    n += 1; snap(d, n, "battery", epoch(12, 7), batteryChargeLevelPercentage=70, chargingStatusV2="CHARGING_STATUS_V2_IDLE")
    n += 1; snap(d, n, "battery", epoch(12, 8), batteryChargeLevelPercentage=64, chargingStatusV2="CHARGING_STATUS_V2_IDLE")
    for k in range(5):
        n += 1; snap(d, n, "battery", epoch(13, 23, 15 * k), batteryChargeLevelPercentage=64 + 4 * k,
                     chargingStatusV2="CHARGING_STATUS_V2_CHARGING", chargingPowerWatts=7000, chargingType="CHARGING_TYPE_AC")
    n += 1; snap(d, n, "location", epoch(12, 6), coordinate={"latitude": 50.1234, "longitude": 3.5678})
    d.commit()
    return d


def test_week_helpers():
    assert stats.week_start("2026-W20") == dt.date(2026, 5, 11)
    assert stats.week_id(dt.date(2026, 5, 17)) == "2026-W20"
    # Wednesday 20 May 2026: the last full week is 11-17 May
    assert stats.last_full_week(dt.datetime(2026, 5, 20, 12)) == "2026-W20"
    # Sunday evening 17 May: that week has not ended yet, so the previous one
    assert stats.last_full_week(dt.datetime(2026, 5, 17, 23)) == "2026-W19"


def test_weekly_stats_contents_and_privacy(db):
    s = stats.weekly(db, "2026-W20")
    assert s["period"] == {"start": "2026-05-11", "end": "2026-05-17"}
    assert s["driving"]["trips"] == 1 and s["driving"]["km"] == 30.0
    assert s["charging"]["sessions"] == 1 and s["charging"]["peak_kw"] == 7.0
    assert s["previous_week_driving"]["trips"] == 0
    blob = json.dumps(s)
    assert VIN not in blob and "latitude" not in blob and "3.5678" not in blob and "50.1234" not in blob


def test_weekly_stats_use_a_manual_cost(db):
    assert stats.weekly(db, "2026-W20")["charging"]["cost_eur"] is None          # no price configured: unknown
    costs.set_cost(db, int(epoch(13, 23)), 6.4)
    s = stats.weekly(db, "2026-W20")["charging"]
    assert s["cost_eur"] == 6.4 and s["potential_saving_eur"] == 0


def test_report_store_roundtrip(db):
    assert not report.exists(db, "2026-W20")
    report.store(db, "2026-W20", "## Hi\n- a\n", {"week": "2026-W20"}, "sonnet")
    assert report.exists(db, "2026-W20")
    assert report.list_weeks(db)[0]["week"] == "2026-W20"
    got = report.get(db, "2026-W20")
    assert got["markdown"].startswith("## Hi") and got["model"] == "sonnet"


def test_generate_with_fake_cli(db, tmp_path, monkeypatch):
    prompt_log = tmp_path / "prompt.txt"
    fake = tmp_path / "fake_claude.sh"
    fake.write_text(f'#!/bin/sh\ncat > "{prompt_log}"\nprintf "## Fake week\\n- ok\\n"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CLAUDE_BIN", str(fake))
    md = report.generate(db, "2026-W20")
    assert md.startswith("## Fake week")
    assert '"week": "2026-W20"' in prompt_log.read_text() and VIN not in prompt_log.read_text()
    assert report.get(db, "2026-W20")["model"] == "sonnet"
    # second call without force reuses the stored report (the fake would otherwise be re-run)
    prompt_log.unlink()
    report.generate(db, "2026-W20")
    assert not prompt_log.exists()


def test_cli_failure_is_reported(db, tmp_path, monkeypatch):
    bad = tmp_path / "bad.sh"
    bad.write_text("#!/bin/sh\nexit 3\n")
    bad.chmod(bad.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CLAUDE_BIN", str(bad))
    with pytest.raises(RuntimeError):
        report.generate(db, "2026-W20")
    assert not report.exists(db, "2026-W20")
