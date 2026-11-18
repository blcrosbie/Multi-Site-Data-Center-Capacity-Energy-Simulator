"""Smoke and interaction tests: every page renders under every predefined scenario, and the
levers on Scenario Planning / Executive Decisions flow through to every page."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ui.state import PRESETS

APP = Path(__file__).resolve().parent.parent / "app.py"
PAGES = ["views/overview.py", "views/forecasting.py", "views/scenarios.py", "views/decisions.py"]
TITLES = dict(zip(PAGES, ["Executive Overview", "Capacity Forecasting", "Scenario Planning", "Executive Decisions"]))


def _app():
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    return at


def _page(at, page):
    at.switch_page(page).run()
    assert not at.exception, at.exception
    return at


@pytest.mark.parametrize("page", PAGES)
def test_pages_render(page):
    at = _page(_app(), page)
    assert any("synthetic data" in m.value.lower() for m in at.markdown)
    assert at.title[0].value == TITLES[page]
    assert not at.error


@pytest.mark.parametrize("preset", PRESETS)
def test_every_page_under_each_preset(preset):
    at = _app()
    at.button(key=f"preset_{preset}").click().run()
    assert at.session_state["preset"] == preset
    for page in PAGES:
        _page(at, page)
        assert not at.error, [e.value for e in at.error]


def test_reset_to_baseline():
    at = _app()
    at.button(key="preset_High Growth").click().run()
    assert at.session_state["pg_on"]
    at.button(key="reset").click().run()
    assert at.session_state["preset"] == "Baseline"
    assert not at.session_state["pg_on"]


def test_invalid_thresholds_show_clear_error():
    at = _app()
    at.slider(key="warn_pct").set_value(95).run()
    at.slider(key="crit_pct").set_value(90).run()
    assert not at.exception
    assert any("can't be modeled" in e.value for e in at.error)


def test_scenario_levers_mark_custom_and_persist_across_pages():
    at = _page(_app(), "views/scenarios.py")
    at.slider(key="eff_pct").set_value(20).run()
    assert at.session_state["preset"] == "Custom"
    _page(at, "views/overview.py")
    assert at.session_state["eff_pct"] == 20
    assert any("efficiency:" in m.value for m in at.markdown)  # status bar shows the lever


def test_site_growth_overrides_prefill_and_apply():
    at = _page(_app(), "views/scenarios.py")
    at.toggle(key="sg_on").set_value(True).run()
    assert not at.exception
    assert at.session_state["sg_DC3"] == pytest.approx(28.2)
    at.number_input(key="sg_DC3").set_value(5.0).run()
    assert not at.exception
    _page(at, "views/forecasting.py")


def test_autosize_expansion_for_selected_site():
    at = _page(_app(), "views/scenarios.py")
    at.selectbox(key="exp_site").set_value("DC3").run()
    at.button[[b.label for b in at.button].index("Size it for me")].click().run()
    assert not at.exception
    assert at.session_state["exp_power"] > 0 and at.session_state["exp_site"] == "DC3"


def test_budget_toggle_on_decisions_page_flows_to_overview():
    at = _page(_app(), "views/decisions.py")
    at.toggle(key="budget_on").set_value(True).run()
    assert not at.exception
    assert any("budget:" in m.value for m in at.markdown)
    _page(at, "views/overview.py")
    assert at.session_state["budget_on"]


def test_deferral_slider_and_short_horizon():
    at = _app()
    at.select_slider(key="horizon").set_value(6).run()
    _page(at, "views/decisions.py")
    at.slider(key="dec_delay").set_value(12).run()
    assert not at.exception
    _page(at, "views/forecasting.py")
    _page(at, "views/scenarios.py")


def test_invalid_monte_carlo_settings_are_reported_not_raised():
    at = _page(_app(), "views/scenarios.py")
    at.slider(key="mc_slip_max").set_value(0).run()   # most likely (1) > worst case (0)
    assert not at.exception
    assert any("can't be run" in e.value for e in at.error)
    _page(at, "views/forecasting.py")
    assert any("Monte Carlo settings are invalid" in w.value for w in at.warning)
