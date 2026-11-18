"""Decision support, sustainability and exports.

Several cases re-check the hand-worked numbers in GUIDE.md §17 against the engine,
so the field guide and the app cannot drift apart.
"""

import pandas as pd
import pytest

from dcsim import config as C
from dcsim import export, rules
from dcsim import scenario as S
from dcsim.capital import plan_capital
from dcsim.decisions import analyze, deferral_consequences
from dcsim.montecarlo import MCSettings, run_monte_carlo
from dcsim.sustainability import carbon_contrast, footprint


@pytest.fixture
def base_plan(real_sites, real_planned):
    return plan_capital(real_sites, real_planned, S.baseline())


@pytest.fixture
def base_da(base_plan, real_sites, real_planned):
    return analyze(base_plan, real_sites, real_planned)


# --- GUIDE §17 worked math ------------------------------------------------------------

def test_guide_crashing_dc3_cuts_gap_from_four_to_two_months(base_da):
    r = base_da.crash.set_index(["site_id", "resource"]).loc[("DC3", "power")]
    assert r.exhaustion_month == 8 and r.activation_month == 12
    assert r.crashed_activation_month == 10           # ceil(12 x 0.77) = 10
    assert (r.gap_months_on_time, r.gap_months_crashed) == (4, 2)


def test_guide_deferring_dc6_phase2_power_is_worth_3m(base_da):
    d = base_da.deferrable.set_index("expansion_id")
    assert set(d.index) == {"EXP-601", "EXP-602", "EXP-603"}  # all of DC6 Phase 2, nothing else
    assert d.loc["EXP-601", "pv_value_defer_12m"] == pytest.approx(40e6 / 1.1**2 - 40e6 / 1.1**3, rel=1e-9)
    assert d.loc["EXP-601", "pv_value_defer_12m"] == pytest.approx(3.0e6, abs=0.05e6)
    assert d.loc["EXP-601", "peak_utilization_without"] == pytest.approx(0.64, abs=0.01)


def test_guide_pue_improvement_at_dc4_frees_1_5_mw(base_da):
    r = base_da.pue.set_index("site_id").loc["DC4"]
    assert r.freed_mw == pytest.approx(11.69 * (1 - 1.35 / 1.55))
    assert r.freed_mw == pytest.approx(1.5, abs=0.05)
    assert r.limiting_resource == "space"


def test_guide_carbon_and_water_run_rates(base_plan):
    f = footprint(base_plan.before, 0).set_index("site_id")
    assert f.loc["DC1", "annual_emissions_tco2e"] == pytest.approx(11_500, rel=0.01)
    assert f.loc["DC3", "annual_emissions_tco2e"] == pytest.approx(67_900, rel=0.01)
    assert f.loc["DC2", "annual_water_ml"] == pytest.approx(274, rel=0.01)
    cc = carbon_contrast(base_plan.before)
    assert (cc["clean"]["site_id"], cc["dirty"]["site_id"]) == ("DC1", "DC3")
    assert cc["power_ratio"] == pytest.approx(1.18, abs=0.01) and cc["emissions_ratio"] == pytest.approx(5.9, abs=0.05)


# --- Decision analysis ------------------------------------------------------------------

def test_deferring_dc3_power_six_months_adds_six_months_over_capacity(base_plan):
    d = deferral_consequences(base_plan, 6).set_index("item")
    r = d.loc["#3 DC3 electrical power"]
    assert (r.months_over_capacity_on_time, r.months_over_capacity_deferred) == (4, 10)
    assert r.extra_months_over_capacity == 6


def test_residual_gap_and_cleaner_destination(base_da):
    g = base_da.gaps
    assert list(zip(g["site_id"], g["resource"])) == [("DC3", "power")]
    assert (g["first_month"].iat[0], g["last_month"].iat[0]) == (8, 11)
    m = base_da.migration
    assert m["source_id"].eq("DC3").all() and m["dest_id"].iat[0] == "DC1"   # cleanest site that can carry the gap
    assert m["covers_need"].iat[0] and (m["emissions_saved_tco2e"] > 0).all()


def test_no_projects_needed_does_not_crash(real_sites, real_planned):
    sc = S.baseline(portfolio_growth=-0.3, warning_threshold=0.95, critical_threshold=0.99, horizon=6, budget_usd=1e7)
    plan = plan_capital(real_sites, real_planned, sc)
    assert plan.projects.empty and plan.proposed_total == 0
    da = analyze(plan, real_sites, real_planned)
    ins = rules.decision_insights(da)
    assert all(ins[k] for k in rules.CATEGORIES)
    assert "No new capacity" in rules.decision_headline(da)[1]
    assert export.capital_comparison(plan).empty


# --- Rules -----------------------------------------------------------------------------

def test_every_category_has_insights_and_every_insight_cites_a_basis(base_da):
    ins = rules.decision_insights(base_da)
    assert list(ins) == list(rules.CATEGORIES)
    for items in ins.values():
        assert items
        assert all(i.basis and i.title and i.severity in rules.SEVERITY_ORDER for i in items)


def test_budget_constrained_flags_unfunded_dc3_power(real_sites, real_planned):
    plan = plan_capital(real_sites, real_planned, S.budget_constrained())
    da = analyze(plan, real_sites, real_planned)
    titles = [i.title for i in rules.decision_insights(da)["budget"]]
    assert any("DC3 electrical power" in t and "exceeds the remaining budget" in t for t in titles)
    eyebrow, head, detail, status = rules.decision_headline(da)
    assert "budget funds" in detail


def test_headline_and_briefing_name_the_earliest_constraint(base_da):
    _, head, detail, _ = rules.decision_headline(base_da)
    assert head.startswith("Commit 5 expansion project(s) now")
    notes = dict(rules.briefing(base_da))
    ops = notes["For an operations director"]
    assert "Prairie Junction runs out of electrical power in Jun 2027" in ops
    assert "4-month gap" in ops and "Granite Pass" in ops
    assert "Basalt Ridge" in notes["For a sustainability lead"]


def test_mc_insights_attribute_dc2_near_term_risk_to_schedule_slip(real_sites, real_planned):
    mc = run_monte_carlo(real_sites, real_planned, S.baseline())
    mc0 = run_monte_carlo(real_sites, real_planned, S.baseline(), MCSettings(slip_mode=0, slip_max=0))
    dc2 = [i for i in rules.mc_insights(mc, mc0, limit=10) if i.site_id == "DC2" and "electrical power" in i.title]
    assert dc2 and "Over the next 24 months the risk is schedule slip" in dc2[0].detail


# --- Exports ----------------------------------------------------------------------------

def test_monthly_export_labels_actual_vs_projected(base_plan):
    m = export.monthly_forecast(base_plan.before)
    assert len(m) == 6 * 3 * (C.MAX_HORIZON + 1)
    assert set(m.loc[m["month"] == 0, "data_type"]) == {"Actual (synthetic baseline)"}
    assert set(m.loc[m["month"] > 0, "data_type"]) == {"Projected"}
    assert {"above_warning", "over_capacity", "scenario"} <= set(m.columns)


def test_facility_and_capital_exports(base_plan):
    f = export.facility_capacity(base_plan.before)
    assert len(f) == 18 and f["first_limiting_resource"].sum() == 6
    c = export.capital_comparison(base_plan)
    assert len(c) == len(base_plan.projects) and {"priority_score", "commit_by_period", "funded"} <= set(c.columns)
    assert export.to_csv_bytes(c)[:3] == b"\xef\xbb\xbf"  # BOM for Excel


def test_scenario_export_has_assumptions_kpis_sites_and_capital(real_sites, real_planned):
    plan = plan_capital(real_sites, real_planned, S.high_growth())
    df = export.scenario_results(plan.before, plan)
    assert set(df["section"]) == {"assumption", "portfolio_kpi", "site_result", "capital"}
    a = df[df["section"] == "assumption"].set_index("item")["value"]
    assert a["scenario"] == "High Growth" and a["portfolio_growth_override"] == 0.25
    assert (df["item"] == "risk_status").sum() == 6


def test_executive_summary_markdown(base_da, base_plan):
    ins = rules.decision_insights(base_da)
    md = export.executive_summary_md(base_plan.scenario, rules.decision_headline(base_da), ins,
                                     {k: k for k in ins}, base_plan, rules.briefing(base_da))
    assert md.startswith("# Six-Site") and "Synthetic data" in md
    assert md.count("Based on:") == sum(len(v) for v in ins.values())
    assert "| 1 | DC4 |" in md


def test_prob_within_matches_summary(real_sites, real_planned):
    mc = run_monte_carlo(real_sites, real_planned, S.baseline())
    es = mc.exhaustion_summary()
    pw = mc.prob_within(36)
    for k, r in enumerate(es.itertuples(index=False)):
        assert pw.ravel()[k] == pytest.approx(r.prob_within_36m)
    assert isinstance(es, pd.DataFrame)
