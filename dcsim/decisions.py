"""Numeric decision analysis behind the Executive Decisions page (SOW Module 6).

Everything here re-uses the deterministic forecast arrays. None of it changes
the official forecast or the capital ranking; it quantifies the options an
executive weighs around them:

* crash_analysis       - late projects: how much a compressed schedule closes the gap
* deferral_consequences- what a project (or planned expansion) slipping N months costs in capacity
* deferrable_planned   - planned expansions the forecast does not need within 36 months
* pue_opportunities    - capacity freed by bringing high-PUE sites to a target PUE
* residual_gaps        - months still over capacity after the funded capital plan
* migration_options    - cleaner sites that could carry a constrained site's flexible load

Assumptions used only in text (discount rate, crash compression, PUE target)
live in config.py and are labelled illustrative wherever they appear.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config as C
from .capital import CapitalPlan
from .forecast import MONTHS, ForecastResult, period_label, run_forecast

P, CL, SP = (C.RESOURCES.index(r) for r in C.RESOURCES)


def _first(mask: np.ndarray) -> int | None:
    idx = np.flatnonzero(mask)
    return int(idx[0]) if len(idx) else None


def _discount(month: float, rate: float) -> float:
    return (1 + rate) ** (-month / 12)


# --- Schedule ---------------------------------------------------------------

def crash_analysis(plan: CapitalPlan, compression: float = C.CRASH_COMPRESSION) -> pd.DataFrame:
    """Projects that arrive after their need date, and what crashing the schedule buys.

    gap = months demand exceeds capacity before the project lands. Crashed lead
    time = ceil(lead x (1 - compression)).
    """
    cols = ["rank", "site_id", "site_name", "resource", "capacity_added", "cost_usd", "need_month",
            "exhaustion_month", "lead_time_months", "activation_month", "crashed_lead_months",
            "crashed_activation_month", "gap_months_on_time", "gap_months_crashed",
            "months_above_warning_on_time", "months_above_warning_crashed", "funded"]
    p = plan.projects
    if p.empty:
        return pd.DataFrame(columns=cols)
    b = plan.before
    rows = []
    for r in p[p["arrives_late"]].itertuples(index=False):
        i, j = b.site_ids.index(r.site_id), C.RESOURCES.index(r.resource)
        d, cap = b.demand[i, j], b.capacity[i, j]
        lead_c = math.ceil(r.lead_time_months * (1 - compression) - 1e-9)
        act_c = max(int(r.need_month), lead_c, 1)
        ex = r.exhaustion_if_not_built
        rows.append({
            "rank": r.rank, "site_id": r.site_id, "site_name": r.site_name, "resource": r.resource,
            "capacity_added": r.capacity_added, "cost_usd": r.cost_usd, "need_month": int(r.need_month),
            "exhaustion_month": None if pd.isna(ex) else int(ex),
            "lead_time_months": int(r.lead_time_months), "activation_month": int(r.activation_month),
            "crashed_lead_months": lead_c, "crashed_activation_month": act_c,
            "gap_months_on_time": int(((d > cap) & (MONTHS < r.activation_month)).sum()),
            "gap_months_crashed": int(((d > cap) & (MONTHS < act_c)).sum()),
            "months_above_warning_on_time": int(r.activation_month - r.need_month),
            "months_above_warning_crashed": max(0, act_c - int(r.need_month)),
            "funded": bool(r.funded),
        })
    return pd.DataFrame(rows, columns=cols)


def deferral_consequences(plan: CapitalPlan, delay: int = C.DEFAULT_DEFERRAL_TEST) -> pd.DataFrame:
    """Each recommended project deferred, and each planned expansion slipped, by `delay` months.

    Measured on the forecast before new projects, over the full 36-month lookahead.
    Only the item being tested moves; everything else stays as planned.
    """
    b = plan.before
    w = plan.scenario.warning_threshold
    items = []
    for r in plan.projects.itertuples(index=False):
        items.append(dict(kind="Recommended", item=f"#{r.rank} {r.site_id} {C.RESOURCE_LABELS[r.resource].lower()}",
                          site_id=r.site_id, resource=r.resource, amount=float(r.capacity_added),
                          activation=int(r.activation_month), cost_usd=float(r.cost_usd), planned=False,
                          rank=int(r.rank), funded=bool(r.funded)))
    for e in b.expansions:
        if e.source == "planned":
            items.append(dict(kind="Planned", item=e.label, site_id=e.site_id, resource=e.resource,
                              amount=float(e.amount), activation=int(e.activation_month),
                              cost_usd=float(e.cost_usd or 0), planned=True, rank=None, funded=True))
    rows = []
    for it in items:
        i, j = b.site_ids.index(it["site_id"]), C.RESOURCES.index(it["resource"])
        d = b.demand[i, j]
        step = lambda m: it["amount"] * (MONTHS >= m)  # noqa: E731
        base = b.capacity[i, j] - (step(it["activation"]) if it["planned"] else 0)
        on, late = base + step(it["activation"]), base + step(it["activation"] + delay)
        window = (MONTHS >= it["activation"]) & (MONTHS < it["activation"] + delay)
        over_on, over_late = int((d > on).sum()), int((d > late).sum())
        warn_on, warn_late = int((d >= w * on).sum()), int((d >= w * late).sum())
        rows.append({
            **{k: it[k] for k in ("kind", "item", "site_id", "resource", "amount", "cost_usd", "rank", "funded")},
            "site_name": b.sites["site_name"].iat[i],
            "unit": C.RESOURCE_UNITS[it["resource"]],
            "activation_month": it["activation"],
            "deferred_activation_month": it["activation"] + delay,
            "beyond_lookahead": it["activation"] + delay > C.MAX_HORIZON,
            "exhaustion_on_time": _first(d > on),
            "exhaustion_deferred": _first(d > late),
            "months_over_capacity_on_time": over_on,
            "months_over_capacity_deferred": over_late,
            "extra_months_over_capacity": over_late - over_on,
            "extra_months_above_warning": warn_late - warn_on,
            "peak_shortfall_deferred": float(np.max(np.where(window, d - late, 0.0), initial=0.0)),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["extra_months_over_capacity", "extra_months_above_warning"],
                          ascending=False).reset_index(drop=True)


# --- Capital ------------------------------------------------------------------

def deferrable_planned(sites: pd.DataFrame, planned: pd.DataFrame | None, plan: CapitalPlan,
                       rate: float = C.DISCOUNT_RATE) -> pd.DataFrame:
    """Planned expansions whose site/resource stays below the warning line for 36 months without them."""
    cols = ["expansion_id", "site_id", "site_name", "resource", "description", "capacity_added", "cost_usd",
            "activation_month", "peak_utilization_without", "utilization_m36_without", "pv_value_defer_12m"]
    sc = plan.scenario
    if planned is None or planned.empty or not sc.include_planned:
        return pd.DataFrame(columns=cols)
    rows = []
    for idx, e in planned.iterrows():
        without = run_forecast(sites, planned.drop(index=idx), sc, validate=False)
        i, j = without.site_ids.index(e.site_id), C.RESOURCES.index(e.resource)
        u = without.demand[i, j] / without.capacity[i, j]
        if u.max() >= sc.warning_threshold:
            continue
        t = float(e.activation_month)
        rows.append({
            "expansion_id": e.expansion_id, "site_id": e.site_id,
            "site_name": without.sites["site_name"].iat[i], "resource": e.resource,
            "description": e.description, "capacity_added": float(e.capacity_added),
            "cost_usd": float(e.cost_usd), "activation_month": int(t),
            "peak_utilization_without": float(u.max()), "utilization_m36_without": float(u[-1]),
            "pv_value_defer_12m": float(e.cost_usd) * (_discount(t, rate) - _discount(t + 12, rate)),
        })
    return pd.DataFrame(rows, columns=cols)


# --- Optimization ---------------------------------------------------------------

def pue_opportunities(result: ForecastResult, target: float = C.PUE_TARGET) -> pd.DataFrame:
    """Electrical capacity freed at high-PUE sites by reaching `target` with the same IT load.

    freed MW = facility MW x (1 - target / PUE); it buys ln(PUE/target)/ln(1+g) years of power growth.
    Cooling plant load is unchanged: the IT heat is the same.
    """
    cols = ["site_id", "site_name", "pue", "target_pue", "facility_mw", "freed_mw", "months_of_growth",
            "avoided_capex_usd", "energy_saved_mwh", "emissions_saved_tco2e", "limiting_resource",
            "power_exhaustion_month"]
    sc, s = result.scenario, result.sites
    lim = result.site_summary.set_index("site_id")["limiting_resource"]
    pex = result.site_resource.set_index(["site_id", "resource"])["exhaustion_month"]
    rows = []
    for i, r in enumerate(s.itertuples(index=False)):
        if r.pue < C.HIGH_PUE or r.pue <= target:
            continue
        mw = float(result.demand[i, P, 0])
        freed = mw * (1 - target / r.pue)
        g = float(result.growth[i])
        months = 12 * math.log(r.pue / target) / math.log(1 + g) if g > 0 else math.inf
        ex = pex.loc[(r.site_id, "power")]
        rows.append({
            "site_id": r.site_id, "site_name": r.site_name, "pue": r.pue, "target_pue": target,
            "facility_mw": mw, "freed_mw": freed, "months_of_growth": months,
            "avoided_capex_usd": freed * sc.cost_per_mw,
            "energy_saved_mwh": freed * C.HOURS_PER_YEAR,
            "emissions_saved_tco2e": freed * C.HOURS_PER_YEAR * r.grid_carbon_t_per_mwh,
            "limiting_resource": lim[r.site_id],
            "power_exhaustion_month": None if pd.isna(ex) else int(ex),
        })
    return pd.DataFrame(rows, columns=cols)


def stranded_power(result: ForecastResult) -> pd.DataFrame:
    """Rack-bound sites still holding electrical headroom they cannot sell (Guide §7)."""
    cols = ["site_id", "site_name", "space_utilization", "power_utilization", "power_headroom_mw"]
    ss = result.site_summary
    w = result.scenario.warning_threshold
    rows = []
    for r in ss.itertuples(index=False):
        if r.limiting_resource == "space" and r.power_utilization < w:
            i = result.site_ids.index(r.site_id)
            rows.append({"site_id": r.site_id, "site_name": r.site_name,
                         "space_utilization": r.space_utilization, "power_utilization": r.power_utilization,
                         "power_headroom_mw": float(result.capacity[i, P, 0] - result.demand[i, P, 0])})
    return pd.DataFrame(rows, columns=cols)


def residual_gaps(result: ForecastResult) -> pd.DataFrame:
    """Site/resource months over capacity within the horizon (run on the post-investment forecast)."""
    h = result.horizon
    rows = []
    for i, sid in enumerate(result.site_ids):
        for j, res in enumerate(C.RESOURCES):
            d, c = result.demand[i, j, : h + 1], result.capacity[i, j, : h + 1]
            over = d > c
            if not over.any():
                continue
            idx = np.flatnonzero(over)
            short = d - c
            rows.append({
                "site_id": sid, "site_name": result.sites["site_name"].iat[i], "resource": res,
                "unit": C.RESOURCE_UNITS[res], "first_month": int(idx[0]), "last_month": int(idx[-1]),
                "months_over_capacity": int(over.sum()), "peak_shortfall": float(short.max()),
                "peak_month": int(np.argmax(short)),
            })
    return pd.DataFrame(rows, columns=["site_id", "site_name", "resource", "unit", "first_month", "last_month",
                                       "months_over_capacity", "peak_shortfall", "peak_month"])


def migration_options(result: ForecastResult, gaps: pd.DataFrame, top: int = 2) -> pd.DataFrame:
    """Cleaner sites that could carry a constrained site's flexible (latency-tolerant) IT load.

    For each site still over power or cooling capacity after the plan: the IT MW
    needed to stay within capacity through the gap window, and for every other
    site without its own gap, the IT MW it can take while staying under the
    warning threshold on power AND cooling for the whole window. Emissions use
    PUE x average grid intensity (location-based); marginal rates may differ.
    """
    cols = ["source_id", "source_name", "window_start", "window_end", "need_it_mw", "need_facility_mw",
            "dest_id", "dest_name", "dest_absorb_it_mw", "moved_it_mw", "covers_need",
            "source_t_per_it_mwh", "dest_t_per_it_mwh", "intensity_ratio", "emissions_saved_tco2e"]
    if gaps.empty:
        return pd.DataFrame(columns=cols)
    sc, h, s = result.scenario, result.horizon, result.sites
    w, hf = sc.warning_threshold, C.COOLING_HEAT_FACTOR
    pue = s["pue"].to_numpy(float)
    eff = pue * s["grid_carbon_t_per_mwh"].to_numpy(float)  # tCO2e per MWh of IT energy
    D, Cap = result.demand[:, :, : h + 1], result.capacity[:, :, : h + 1]
    constrained = set(gaps["site_id"])
    rows = []
    for sid in sorted(set(gaps.loc[gaps["resource"].isin(["power", "cooling"]), "site_id"])):
        i = result.site_ids.index(sid)
        need_m = np.maximum.reduce([(D[i, P] - Cap[i, P]) / pue[i], (D[i, CL] - Cap[i, CL]) / hf,
                                    np.zeros(h + 1)])
        idx = np.flatnonzero(need_m > 0)
        if not len(idx):
            continue
        ws = slice(int(idx[0]), int(idx[-1]) + 1)
        need = float(need_m.max())
        cands = []
        for j, did in enumerate(result.site_ids):
            if did == sid or did in constrained:
                continue
            room_p = float(np.min(w * Cap[j, P, ws] - D[j, P, ws])) / pue[j]
            room_c = float(np.min(w * Cap[j, CL, ws] - D[j, CL, ws])) / hf
            absorb = max(0.0, min(room_p, room_c))
            if absorb < 0.05 or eff[j] >= eff[i]:
                continue
            moved = min(need, absorb)
            cands.append({
                "source_id": sid, "source_name": s["site_name"].iat[i],
                "window_start": int(idx[0]), "window_end": int(idx[-1]),
                "need_it_mw": need, "need_facility_mw": need * pue[i],
                "dest_id": did, "dest_name": s["site_name"].iat[j],
                "dest_absorb_it_mw": absorb, "moved_it_mw": moved, "covers_need": absorb >= need - 1e-9,
                "source_t_per_it_mwh": eff[i], "dest_t_per_it_mwh": eff[j],
                "intensity_ratio": eff[i] / eff[j] if eff[j] > 0 else math.inf,
                "emissions_saved_tco2e": moved * C.HOURS_PER_YEAR * (eff[i] - eff[j]),
            })
        # Prefer a destination that covers the whole need, then the cleanest.
        cands.sort(key=lambda c: (not c["covers_need"], c["dest_t_per_it_mwh"]))
        rows.extend(cands[:top])
    return pd.DataFrame(rows, columns=cols)


# --- Bundle -----------------------------------------------------------------------

@dataclass
class DecisionAnalysis:
    plan: CapitalPlan
    after: ForecastResult          # forecast with the funded plan applied
    delay: int
    crash: pd.DataFrame
    deferral: pd.DataFrame
    deferrable: pd.DataFrame
    pue: pd.DataFrame
    stranded: pd.DataFrame
    gaps: pd.DataFrame
    migration: pd.DataFrame

    @property
    def before(self) -> ForecastResult:
        return self.plan.before


def analyze(plan: CapitalPlan, sites: pd.DataFrame, planned: pd.DataFrame | None,
            delay: int = C.DEFAULT_DEFERRAL_TEST) -> DecisionAnalysis:
    gaps = residual_gaps(plan.after)
    return DecisionAnalysis(
        plan=plan, after=plan.after, delay=delay,
        crash=crash_analysis(plan),
        deferral=deferral_consequences(plan, delay),
        deferrable=deferrable_planned(sites, planned, plan),
        pue=pue_opportunities(plan.before),
        stranded=stranded_power(plan.before),
        gaps=gaps,
        migration=migration_options(plan.after, gaps),
    )


def period(m) -> str:
    """Month index -> 'Jun 2027 (M8)'; tolerant of None / NA."""
    if m is None or pd.isna(m):
        return f"not within {C.MAX_HORIZON} mo"
    return f"{period_label(int(m))} (M{int(m)})"
