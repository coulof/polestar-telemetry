"""Headless smoke test of the dashboard on a synthetic database (made-up places, no real data)."""
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from demo import make_demo_db
from polestar import collect, costs, derive


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("demo") / "demo.db")
    db = collect.connect(path)
    for ts, domain, payload in sorted(make_demo_db.build(time.time() - 120), key=lambda e: e[0]):
        collect.store(db, make_demo_db.VIN, domain, payload)
    db.commit()
    return path


@pytest.fixture
def app(demo_db, monkeypatch):
    monkeypatch.setattr(collect, "DB_PATH", demo_db)
    monkeypatch.setenv("CONFIG_PATH", str(Path(__file__).parent.parent / "demo" / "config.toml"))
    monkeypatch.delenv("POLESTAR_MOCK", raising=False)
    return AppTest.from_file(str(Path(__file__).parent.parent / "app.py"), default_timeout=120)


def test_dashboard_renders_without_exceptions(app):
    at = app.run()
    assert not at.exception
    assert [t.label for t in at.tabs][:8] == ["Now", "Report", "Trips", "Charging", "Battery", "Insights", "Air", "Data"]


def test_mock_tabs_only_with_the_mock_flag(app, monkeypatch):
    monkeypatch.setenv("POLESTAR_MOCK", "1")
    at = app.run()
    assert not at.exception
    assert [t.label for t in at.tabs][8:] == ["Compare (mock)", "Path (mock)"]


def test_battery_capacity_is_per_session_not_a_module_global(app):
    default = derive.BATTERY_KWH
    at = app.run()
    at.sidebar.number_input(key="battery_kwh").set_value(60.0).run()
    assert not at.exception
    assert derive.BATTERY_KWH == default


def test_cost_can_be_set_and_reset_from_the_charging_tab(app, demo_db):
    at = app.run()
    amount = next(n for n in at.number_input if n.label == "Total paid (€)")
    amount.set_value(12.34).run()
    next(b for b in at.button if b.label == "Save").click().run()
    assert not at.exception
    with collect.connect(demo_db) as db:
        saved = costs.load(db)
    assert list(saved.values()) == [12.34]
    assert any("€12.34 (entered manually)" in c.value for c in at.caption)
    next(b for b in at.button if b.label == "Use estimate").click().run()
    assert not at.exception
    with collect.connect(demo_db) as db:
        assert costs.load(db) == {}
