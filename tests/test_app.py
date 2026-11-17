"""Headless smoke test of the dashboard on a synthetic database (made-up places, no real data)."""
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from demo import make_demo_db
from polestar import admin, collect, costs, derive, report


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
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret")
    admin._fails.clear()
    return AppTest.from_file(str(Path(__file__).parent.parent / "app.py"), default_timeout=120)


def test_dashboard_renders_without_exceptions(app):
    at = app.run()
    assert not at.exception
    assert [t.label for t in at.tabs][:8] == ["Now", "Report", "Trips", "Charging", "Battery", "Insights", "Air", "Data"]


def test_report_tab_is_hidden_when_the_integration_is_disabled(app, tmp_path, monkeypatch):
    cfg = tmp_path / "off.toml"
    cfg.write_text((Path(__file__).parent.parent / "demo" / "config.toml").read_text().replace("[report]", "[report]\nenabled = false"))
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    at = app.run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Now", "Trips", "Charging", "Battery", "Insights", "Air", "Data"]


def test_mock_tabs_only_with_the_mock_flag(app, monkeypatch):
    monkeypatch.setenv("POLESTAR_MOCK", "1")
    at = app.run()
    assert not at.exception
    assert [t.label for t in at.tabs][8:] == ["Compare (mock)", "Path (mock)"]


def test_demo_ribbon_only_in_the_demo(app, monkeypatch):
    monkeypatch.delenv("POLESTAR_DEMO", raising=False)
    assert not any('class="demo-ribbon"' in m.value for m in app.run().markdown)
    monkeypatch.setenv("POLESTAR_DEMO", "1")
    at = app.run()
    assert not at.exception
    assert any('class="demo-ribbon"' in m.value and "#quick-start" in m.value for m in at.markdown)


def test_battery_capacity_is_per_session_not_a_module_global(app):
    default = derive.BATTERY_KWH
    at = app.run()
    at.sidebar.number_input(key="battery_kwh").set_value(60.0).run()
    assert not at.exception
    assert derive.BATTERY_KWH == default


def unlock(at, password="s3cret"):
    next(t for t in at.text_input if t.label == "Admin password").set_value(password)
    next(b for b in at.button if b.label == "Unlock").click()
    return at.run()


def test_cost_editing_needs_the_admin_password(app, demo_db):
    at = app.run()
    assert not [n for n in at.number_input if n.label == "Total paid (€)"]     # locked: no amount field, no Save
    assert not [b for b in at.button if b.label == "Save"]
    at = unlock(at, "wrong")
    assert any("Wrong password." in e.value for e in at.error)
    assert not [b for b in at.button if b.label == "Save"]
    with collect.connect(demo_db) as db:
        assert costs.load(db) == {}


def test_editing_is_off_without_a_configured_password(app, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD")
    at = app.run()
    assert not at.exception
    assert any("Editing is off" in c.value for c in at.caption)
    assert not [b for b in at.button if b.label in ("Save", "Unlock")]


def test_cost_can_be_set_and_reset_once_unlocked(app, demo_db):
    at = unlock(app.run())
    assert not at.exception
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
    next(b for b in at.button if b.label == "Lock").click().run()
    assert [b for b in at.button if b.label == "Unlock"] and not [b for b in at.button if b.label == "Save"]


def test_report_tab_warns_about_a_failed_weekly_report(app, demo_db):
    with collect.connect(demo_db) as db:
        report.record_failure(db, "2026-W99", "failed while asking Claude (exit 1)")
    try:
        at = app.run()
        assert not at.exception
        assert any("2026-W99" in w.value and "failed while asking Claude" in w.value for w in at.warning)
    finally:
        with collect.connect(demo_db) as db:
            db.execute("DELETE FROM report_failures")
            db.commit()


def test_period_can_be_the_last_day(app):
    at = app.run()
    sel = next(s for s in at.sidebar.selectbox if s.label == "Period")
    assert sel.options[0] == "Last day" and sel.value == 30
    sel.select(1).run()
    assert not at.exception


def test_login_page_gates_the_dashboard_when_a_password_is_set(app, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "view")
    at = app.run()
    assert not at.exception
    assert len(at.tabs) == 0 and not at.metric and not at.sidebar.selectbox     # nothing of the dashboard
    at.text_input[0].input("wrong")
    at.button[0].click().run()
    assert any("Wrong password" in e.value for e in at.error) and len(at.tabs) == 0
    at.text_input[0].input("view")
    at.button[0].click().run()
    assert not at.exception and len(at.tabs) >= 7
    sign_out = next(b for b in at.sidebar.button if b.label == "Sign out")
    sign_out.click().run()
    assert len(at.tabs) == 0


def test_no_login_page_without_a_dashboard_password(app, monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    at = app.run()
    assert len(at.tabs) >= 7 and not [b for b in at.sidebar.button if b.label == "Sign out"]
