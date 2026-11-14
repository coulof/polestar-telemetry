import httpx
import pytest

from polestar import collect
from polestar.client import DOMAINS
from polestar.collect import connect, store

VIN = "TESTVIN0123456789"


@pytest.fixture
def db(tmp_path):
    return connect(str(tmp_path / "t.db"))


def count(db, domain=None):
    q, a = ("SELECT COUNT(*) FROM snapshots WHERE domain = ?", (domain,)) if domain else ("SELECT COUNT(*) FROM snapshots", ())
    return db.execute(q, a).fetchone()[0]


class FakeClient:
    """Scripted telemetry: `behaviour[domain]` is a payload dict or an exception to raise."""
    def __init__(self, behaviour):
        self.behaviour = behaviour

    def vehicles(self):
        return {"data": [VIN]}

    def telemetry(self, vin, domain):
        b = self.behaviour.get(domain, {"timestamp": {"seconds": 1000}, "metaEventId": f"{domain}-1"})
        if isinstance(b, Exception):
            raise b
        return {"data": b}


def status_error(code):
    return httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://t"), response=httpx.Response(code))


def test_store_dedupes_on_meta_event_id(db):
    p = {"timestamp": {"seconds": 1000}, "metaEventId": "e1", "batteryChargeLevelPercentage": 50}
    assert store(db, VIN, "battery", p) is True
    assert store(db, VIN, "battery", dict(p)) is False
    assert count(db) == 1


def test_store_without_event_id_dedupes_on_content_not_on_time(db, monkeypatch):
    p = {"batteryChargeLevelPercentage": 50}              # no id, no timestamp
    monkeypatch.setattr(collect.time, "time", lambda: 1.0)
    assert store(db, VIN, "battery", p) is True
    monkeypatch.setattr(collect.time, "time", lambda: 2.0)   # a later poll of the same payload
    assert store(db, VIN, "battery", dict(p)) is False
    assert store(db, VIN, "battery", {"batteryChargeLevelPercentage": 51}) is True


def test_collect_once_stores_every_domain_and_commits(db, tmp_path):
    new = collect.collect_once(FakeClient({}), db)
    assert new == {d: 1 for d in DOMAINS}
    assert count(connect(str(tmp_path / "t.db"))) == len(DOMAINS)    # visible from another connection: committed
    assert collect.collect_once(FakeClient({}), db) == {d: 0 for d in DOMAINS}   # same events: nothing new


def test_404_is_skipped_silently_and_other_errors_are_logged(db, capsys):
    new = collect.collect_once(FakeClient({"chargeLocations": status_error(404), "health": status_error(500)}), db)
    assert "chargeLocations" not in new and "health" not in new
    out = capsys.readouterr().out
    assert "health: HTTP 500" in out and "chargeLocations" not in out


def test_timeout_on_one_domain_does_not_lose_the_others(db):
    new = collect.collect_once(FakeClient({"location": httpx.ReadTimeout("slow")}), db)
    assert "location" not in new and new["battery"] == 1


def test_rows_fetched_before_an_unexpected_error_are_still_committed(db, tmp_path):
    with pytest.raises(KeyError):
        collect.collect_once(FakeClient({"exterior": {"metaEventId": "x"}, "health": KeyError("boom")}), db)
    assert count(connect(str(tmp_path / "t.db")), "battery") == 1


def put(db, domain, ts, **p):
    store(db, VIN, domain, {"timestamp": {"seconds": ts}, "metaEventId": f"{domain}{ts}", **p})


def test_is_active_when_charging_or_driving(db):
    assert not collect.is_active(db)
    put(db, "battery", 1, chargingStatusV2="CHARGING_STATUS_V2_CHARGING")
    assert collect.is_active(db)
    put(db, "battery", 2, chargingStatusV2="CHARGING_STATUS_V2_IDLE")
    assert not collect.is_active(db)
    put(db, "availability", 3, usageMode="USAGE_MODE_DRIVING")
    assert collect.is_active(db)


def test_a_poll_that_fetches_data_records_the_time_even_when_nothing_is_new(db, monkeypatch):
    from polestar import freshness
    assert freshness.age_seconds(db) is None                       # before the first poll
    monkeypatch.setattr(collect.time, "time", lambda: 5000.0)
    collect.collect_once(FakeClient({}), db)
    assert freshness.age_seconds(db, now=5600) == 600
    collect.collect_once(FakeClient({}), db)                       # same events again: nothing new, still a live poll
    assert db.execute("SELECT COUNT(*) FROM poll_status").fetchone()[0] == 1


def test_a_poll_where_everything_fails_does_not_count_as_fresh(db):
    from polestar import freshness
    collect.collect_once(FakeClient({d: status_error(500) for d in DOMAINS}), db)
    assert freshness.age_seconds(db) is None
    collect.collect_once(FakeClient({d: status_error(404) for d in DOMAINS}), db)
    assert freshness.age_seconds(db) is None
