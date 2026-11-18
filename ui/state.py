"""Session state: scenario levers, predefined scenarios, and the computed context.

Levers live in st.session_state under the keys in DEFAULTS (plus one growth
override per site, `sg_<site_id>`). Every page builds the same Scenario from
them and runs the same engine, so KPIs stay consistent across pages.

Key groups
* view settings (horizon, thresholds)            - kept by preset buttons
* capital/schedule assumptions, Monte Carlo       - kept by preset buttons
* scenario levers (growth, efficiency, expansion, budget) - set by preset buttons
"Reset to Baseline" restores every group.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from dcsim import config as C
from dcsim.capital import CapitalPlan, plan_capital, size_projects
from dcsim.data import load_planned_expansions, load_sites
from dcsim.decisions import DecisionAnalysis, analyze
from dcsim.forecast import ForecastResult
from dcsim.montecarlo import MCResult, MCSettings, run_monte_carlo
from dcsim.scenario import Expansion, Scenario
from dcsim.validation import ValidationError

PRESETS = ("Baseline", "High Growth", "Infrastructure Expansion", "Efficiency Optimization", "Budget-Constrained")

DEFAULTS = {
    "preset": "Baseline",
    # planning assumptions (view settings - presets keep these)
    "horizon": C.MAX_HORIZON,
    "warn_pct": int(C.DEFAULT_WARNING_THRESHOLD * 100),
    "crit_pct": int(C.DEFAULT_CRITICAL_THRESHOLD * 100),
    # scenario levers
    "pg_on": False,
    "pg_pct": 15.0,
    "sg_on": False,
    "eff_pct": 0,
    "include_planned": True,
    "budget_on": False,
    "budget_m": 60.0,
    "exp_site": "DC3",
    "exp_power": 0.0,
    "exp_cooling": 0.0,
    "exp_racks": 0,
    "exp_month": 12,
    # capital & schedule assumptions (SOW Module 5 - presets keep these)
    "cost_mw_m": C.DEFAULT_COST_PER_MW / 1e6,
    "cost_mwt_m": C.DEFAULT_COST_PER_MWT / 1e6,
    "cost_rack_k": C.DEFAULT_COST_PER_RACK / 1e3,
    "lead_power": C.DEFAULT_LEAD_TIME["power"],
    "lead_cooling": C.DEFAULT_LEAD_TIME["cooling"],
    "lead_space": C.DEFAULT_LEAD_TIME["space"],
    # Monte Carlo settings (presets keep these)
    "mc_n": MCSettings.n_draws,
    "mc_sd": MCSettings.growth_sd * 100,
    "mc_corr": MCSettings.growth_corr,
    "mc_slip_mode": int(MCSettings.slip_mode),
    "mc_slip_max": int(MCSettings.slip_max),
    "mc_eff_low": int(MCSettings.eff_realization_low * 100),
}
VIEW_KEYS = ("horizon", "warn_pct", "crit_pct")
ASSUMPTION_KEYS = ("cost_mw_m", "cost_mwt_m", "cost_rack_k", "lead_power", "lead_cooling", "lead_space")
MC_KEYS = ("mc_n", "mc_sd", "mc_corr", "mc_slip_mode", "mc_slip_max", "mc_eff_low")
KEEP_ON_PRESET = VIEW_KEYS + ASSUMPTION_KEYS + MC_KEYS


@st.cache_data(show_spinner=False)
def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    sites = load_sites()
    return sites, load_planned_expansions(site_ids=sites["site_id"].tolist())


def site_ids() -> list[str]:
    return load_data()[0]["site_id"].tolist()


def all_defaults() -> dict:
    """DEFAULTS plus one growth-override lever per site, pre-filled with the site's data growth."""
    sites, _ = load_data()
    return {**DEFAULTS, **{f"sg_{r.site_id}": float(r.annual_growth_pct) for r in sites.itertuples(index=False)}}


def lever_keys() -> list[str]:
    """Keys that change the scenario (and therefore the capital plan)."""
    return [k for k in all_defaults() if k not in MC_KEYS]


def ensure_state() -> None:
    """Initialise defaults and keep lever values alive across page switches.

    Re-assigning each key detaches it from widget clean-up, so a lever set on the
    Scenario Planning page survives a visit to the Overview page.
    """
    ss = st.session_state
    for k, v in all_defaults().items():
        ss[k] = ss[k] if k in ss else v


def _reset_levers(keep_view: bool = True) -> None:
    ss = st.session_state
    for k, v in all_defaults().items():
        if keep_view and k in KEEP_ON_PRESET:
            continue
        ss[k] = v


def current_levers() -> dict:
    ss = st.session_state
    return {k: ss[k] for k in all_defaults()}


def preset_levers(name: str) -> dict:
    """The full lever set a preset button produces, keeping the current view, cost and MC settings."""
    ss = st.session_state
    lv = all_defaults()
    lv.update({k: ss[k] for k in KEEP_ON_PRESET if k in ss})
    lv["preset"] = name
    if name == "High Growth":
        lv.update(pg_on=True, pg_pct=25.0)
    elif name == "Efficiency Optimization":
        lv["eff_pct"] = 10
    elif name == "Budget-Constrained":
        lv.update(budget_on=True, budget_m=60.0)
    elif name == "Infrastructure Expansion":
        lv.update(sized_expansion(lv))
    return lv


def sized_expansion(lv: Mapping, site: str | None = None) -> dict:
    """Expansion levers sized for `site` (default: the earliest constraint) under `lv` without its own expansion."""
    base = {**lv, "exp_power": 0.0, "exp_cooling": 0.0, "exp_racks": 0, "budget_on": False}
    before = plan_for(base).before
    if site is None:
        ec = before.kpis["earliest_constraint"]
        site = ec["site_id"] if ec else "DC3"
    p = size_projects(before)
    p = p[p["site_id"] == site].set_index("resource")
    return {
        "exp_site": site,
        "exp_power": float(p.loc["power", "capacity_added"]) if "power" in p.index else 0.0,
        "exp_cooling": float(p.loc["cooling", "capacity_added"]) if "cooling" in p.index else 0.0,
        "exp_racks": int(p.loc["space", "capacity_added"]) if "space" in p.index else 0,
        "exp_month": int(p["activation_month"].max()) if len(p) else int(lv.get("exp_month", 12)),
    }


def apply_preset(name: str) -> None:
    """Callback for the predefined scenario buttons."""
    st.session_state.update(preset_levers(name))


def reset_to_baseline() -> None:
    _reset_levers(keep_view=False)


def mark_custom() -> None:
    """on_change for scenario levers: the scenario no longer matches a preset."""
    st.session_state["preset"] = "Custom"


def toggle_site_growth() -> None:
    """Turning site overrides on pre-fills each site with the growth currently applied to it."""
    ss = st.session_state
    mark_custom()
    if ss["sg_on"]:
        sites, _ = load_data()
        for r in sites.itertuples(index=False):
            ss[f"sg_{r.site_id}"] = float(ss["pg_pct"]) if ss["pg_on"] else float(r.annual_growth_pct)


def autosize_expansion() -> None:
    """Callback: size the scenario expansion for the selected site."""
    ss = st.session_state
    try:
        ss.update(sized_expansion(current_levers(), site=ss["exp_site"]))
        sized = ss["exp_power"] or ss["exp_cooling"] or ss["exp_racks"]
        ss["_autosize_note"] = None if sized else f"{ss['exp_site']} needs no expansion to stay under the warning threshold."
    except ValidationError as e:
        ss["_autosize_note"] = "Can't size: " + " ".join(e.errors)
    mark_custom()


def build_scenario(lv: Mapping, name: str | None = None) -> Scenario:
    adds = tuple(
        Expansion(lv["exp_site"], res, float(amt), int(lv["exp_month"]),
                  label=f"{lv['exp_site']} {C.RESOURCE_LABELS[res].lower()} expansion (scenario)")
        for res, amt in (("power", lv["exp_power"]), ("cooling", lv["exp_cooling"]), ("space", lv["exp_racks"]))
        if amt and amt > 0
    )
    site_growth = ({k[3:]: float(v) / 100 for k, v in lv.items() if k.startswith("sg_DC")}
                   if lv["sg_on"] else {})
    return Scenario(
        name=name or lv["preset"],
        horizon=int(lv["horizon"]),
        portfolio_growth=lv["pg_pct"] / 100 if (lv["pg_on"] and not lv["sg_on"]) else None,
        site_growth=site_growth,
        efficiency=lv["eff_pct"] / 100,
        include_planned=bool(lv["include_planned"]),
        added_expansions=adds,
        budget_usd=lv["budget_m"] * 1e6 if lv["budget_on"] else None,
        warning_threshold=lv["warn_pct"] / 100,
        critical_threshold=lv["crit_pct"] / 100,
        cost_per_mw=float(lv["cost_mw_m"]) * 1e6,
        cost_per_mwt=float(lv["cost_mwt_m"]) * 1e6,
        cost_per_rack=float(lv["cost_rack_k"]) * 1e3,
        lead_time={"power": int(lv["lead_power"]), "cooling": int(lv["lead_cooling"]), "space": int(lv["lead_space"])},
    )


@st.cache_data(show_spinner=False, max_entries=96)
def _plan_cached(key: tuple) -> CapitalPlan:
    sites, planned = load_data()
    return plan_capital(sites, planned, build_scenario(dict(key)))


def plan_for(lv: Mapping) -> CapitalPlan:
    """Capital plan (and both forecasts) for a lever set; cached on the lever values. Raises ValidationError."""
    return _plan_cached(tuple((k, lv[k]) for k in lever_keys()))


def displayed(plan: CapitalPlan) -> ForecastResult:
    """The forecast a page shows: funded projects applied when a budget is set."""
    return plan.after if plan.budget_usd is not None else plan.before


def preset_plan(name: str) -> CapitalPlan:
    return plan_for(preset_levers(name))


@dataclass
class Context:
    scenario: Scenario
    sites: pd.DataFrame
    planned: pd.DataFrame
    result: ForecastResult     # what every page displays
    plan: CapitalPlan          # capital plan (budget applied if set)
    needs: pd.DataFrame        # expansion each site/resource still needs, sized on `result`


def context() -> Context:
    """Build the scenario and run the engine. Invalid inputs stop the page with clear messages."""
    sites, planned = load_data()
    try:
        plan = plan_for(current_levers())
    except ValidationError as e:
        st.error("**These inputs can't be modeled:**\n\n" + "\n".join(f"- {m}" for m in e.errors))
        st.stop()
    result = displayed(plan)
    return Context(plan.scenario, sites, planned, result, plan, size_projects(result))


def mc_settings(slip: bool = True) -> MCSettings:
    ss = st.session_state
    return MCSettings(
        n_draws=int(ss["mc_n"]), growth_sd=float(ss["mc_sd"]) / 100, growth_corr=float(ss["mc_corr"]),
        slip_mode=float(ss["mc_slip_mode"]) if slip else 0.0, slip_max=float(ss["mc_slip_max"]) if slip else 0.0,
        eff_realization_low=float(ss["mc_eff_low"]) / 100,
    )


def monte_carlo(scenario: Scenario, slip: bool = True) -> MCResult:
    """Monte Carlo on `scenario` with the session's settings. Raises ValidationError on bad settings."""
    sites, planned = load_data()
    return run_monte_carlo(sites, planned, scenario, mc_settings(slip))


def decision_analysis(ctx: Context, delay: int) -> DecisionAnalysis:
    return analyze(ctx.plan, ctx.sites, ctx.planned, delay)

