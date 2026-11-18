"""Deterministic, rules-based executive insights.

Every insight cites the modeled metrics it is based on, so a reader can trace
it back to the forecast tables. No AI is involved.

* overview_insights  - headline insights for the Executive Overview
* decision_insights  - the Executive Decisions panel, grouped by the six SOW Module 6 categories
* decision_headline  - the one-line capital decision for this month
* mc_insights        - where the Monte Carlo view disagrees with the single-path forecast
* briefing           - "how to say it in a meeting" notes, filled from live numbers (Guide §19)
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import config as C
from .forecast import ForecastResult, period_label

SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Info": 3, "Low": 4}
LOW_UTILIZATION = C.LOW_UTILIZATION  # below this, a site is a candidate to absorb migrated load
HIGH_PUE = C.HIGH_PUE


@dataclass(frozen=True)
class Insight:
    severity: str          # Critical | High | Medium | Info | Low
    title: str
    detail: str
    action: str | None
    basis: str             # which modeled metrics / rules the insight rests on
    site_id: str | None = None


def _unit(res: str) -> str:
    return C.RESOURCE_UNITS[res]


def _res(res: str) -> str:
    return C.RESOURCE_LABELS[res].lower()


def fmt_amount(x: float, res: str) -> str:
    return f"{x:,.0f} racks" if res == "space" else f"{x:,.1f} {_unit(res)}"


def project_action(p: pd.Series) -> str:
    amount = fmt_amount(p.capacity_added, p.resource)
    cost = f"~${p.cost_usd / 1e6:,.1f}M"
    if p.commit_by_month <= 0:
        when = "Commit now"
    else:
        when = f"Commit by {period_label(p.commit_by_month)}"
    text = f"{when}: add {amount} of {_res(p.resource)} ({cost}), live {period_label(p.activation_month)}."
    if p.arrives_late:
        text += (f" Lead time ({p.lead_time_months} mo) means it lands after the need date "
                 f"({period_label(p.need_month)}) — bridge with load placement or deferral of new deployments.")
    return text


def overview_insights(result: ForecastResult, projects: pd.DataFrame) -> list[Insight]:
    """Headline insights for the Executive Overview.

    `projects` is capital.size_projects(result): the expansion each site/resource
    needs to stay at or below the warning threshold through the horizon.
    """
    sc = result.scenario
    out: list[Insight] = []
    ss = result.site_summary.set_index("site_id")
    sr = result.site_resource.set_index(["site_id", "resource"])
    k = result.kpis

    def action_for(sid, res):
        if projects.empty:
            return None
        match = projects[(projects["site_id"] == sid) & (projects["resource"] == res)]
        return project_action(match.iloc[0]) if len(match) else None

    # 1. Earliest site-level constraint.
    ec = k["earliest_constraint"]
    if ec and ec["kind"] == "exhaustion":
        sid, res, m = ec["site_id"], ec["resource"], ec["month"]
        row = sr.loc[(sid, res)]
        d_m, c_m = result.demand[result.site_ids.index(sid), C.RESOURCES.index(res), m], \
            result.capacity[result.site_ids.index(sid), C.RESOURCES.index(res), m]
        out.append(Insight(
            severity=ss.loc[sid, "risk_status"],
            title=f"{ec['site_name']} ({sid}) runs out of {_res(res)} in {ec['period']}",
            detail=(f"{row.current_utilization:.0%} utilized today, demand growing "
                    f"{ss.loc[sid, 'annual_growth']:.1%}/yr. In month {m}, modeled demand reaches "
                    f"{fmt_amount(d_m, res)} against {fmt_amount(c_m, res)} of capacity."),
            action=action_for(sid, res),
            basis="Earliest month where forecast demand exceeds available capacity (incl. planned expansions).",
            site_id=sid,
        ))

        # 2. Portfolio totals mask the site constraint.
        pe = k["portfolio_exhaustion_month"][res]
        if pe is None or pe > m:
            n_sites = int(((sr["exhaustion_month"].notna()) &
                           (sr.index.get_level_values("resource") == res)).sum())
            dem0 = result.demand[:, C.RESOURCES.index(res), 0].sum()
            cap0 = result.capacity[:, C.RESOURCES.index(res), 0].sum()
            when = "does not run out within 36 months" if pe is None else f"runs out only in {period_label(pe)}"
            out.append(Insight(
                severity="Info",
                title=f"Portfolio totals hide site-level {_res(res)} constraints",
                detail=(f"Portfolio {_res(res)} is {dem0 / cap0:.0%} utilized and {when}, yet {n_sites} "
                        f"site(s) exhaust {_res(res)} earlier. Spare capacity at one facility does not "
                        f"serve load at another without migration."),
                action=None,
                basis="Portfolio utilization = sum of demand / sum of capacity; compared with site-level exhaustion.",
            ))

    # 3. Other sites at High / Critical risk.
    flagged = ss[ss["risk_status"].isin(["High", "Critical"])]
    for sid, r in flagged.iterrows():
        if ec and sid == ec["site_id"]:
            continue
        res = r["limiting_resource"]
        out.append(Insight(
            severity=r["risk_status"],
            title=f"{r['site_name']} ({sid}) is {r['risk_status'].lower()} risk — {_res(res)} constrained",
            detail=f"Rule triggered: {r['risk_reason']}.",
            action=action_for(sid, res),
            basis=f"Risk rules with warning {sc.warning_threshold:.0%} / critical {sc.critical_threshold:.0%} thresholds.",
            site_id=sid,
        ))

    # 4. Optimization: under-used sites could absorb load from constrained ones.
    if len(flagged):
        p = C.RESOURCES.index("power")
        for sid, r in ss.iterrows():
            if r["risk_status"] == "Low" and r["max_current_utilization"] < LOW_UTILIZATION:
                i = result.site_ids.index(sid)
                head_h = result.capacity[i, p, sc.horizon] - result.demand[i, p, sc.horizon]
                out.append(Insight(
                    severity="Info",
                    title=f"{r['site_name']} ({sid}) has room to absorb migrated load",
                    detail=(f"Peak current utilization is {r['max_current_utilization']:.0%}; "
                            f"{head_h:,.1f} MW power headroom remains at month {sc.horizon}. Placing new "
                            f"deployments here can defer spend at {', '.join(flagged.index)}."),
                    action=None,
                    basis=f"Low-risk sites with all resources below {LOW_UTILIZATION:.0%} utilization today.",
                    site_id=sid,
                ))

    # 5. Efficiency opportunity at high-PUE sites.
    high_pue = result.sites[result.sites["pue"] >= HIGH_PUE]
    if len(high_pue) and sc.efficiency == 0:
        names = ", ".join(f"{r.site_id} (PUE {r.pue:.2f})" for r in high_pue.itertuples())
        out.append(Insight(
            severity="Info",
            title="Efficiency upgrades are an alternative to building",
            detail=(f"{names} run well above the portfolio's best PUE of {result.sites['pue'].min():.2f}. "
                    "Try the Efficiency Optimization scenario to see the deferral effect."),
            action=None,
            basis=f"Sites with PUE >= {HIGH_PUE:.2f}.",
        ))

    return sorted(out, key=lambda x: SEVERITY_ORDER.get(x.severity, 9))


# =============================================================================
# Executive Decisions (SOW Module 6)
# =============================================================================

CATEGORIES = {
    "immediate": "Immediate attention",
    "at_risk": "Facilities at risk",
    "timing": "Expansion timing",
    "optimization": "Optimization",
    "budget": "Over budget",
    "deferral": "Deferral consequences",
}
CATEGORY_HELP = {
    "immediate": "Sites requiring immediate attention: projects whose commit-by date has already passed.",
    "at_risk": "Facilities at risk of reaching capacity limits within 36 months, with the rule that fired.",
    "timing": "Recommended expansion timing: commit-by dates, late arrivals and what crashing the schedule buys.",
    "optimization": "Opportunities for capacity optimization: deferrable spend, efficiency, stranded capacity, load placement.",
    "budget": "Investments exceeding the available budget and what leaving them unfunded does.",
    "deferral": "Potential consequences of deferring infrastructure expansion or letting a planned project slip.",
}


def _usd(x: float) -> str:
    return f"${x / 1e6:,.1f}M"


def _mo(m) -> str:
    """Month index -> 'Jun 2027 (M8)'; tolerant of None / NA."""
    if m is None or pd.isna(m):
        return f"not within {C.MAX_HORIZON} months"
    return f"{period_label(int(m))} (M{int(m)})"


def _reaches(w: float, m) -> str:
    """'is already at 80%' for month 0, else 'reaches 80% Apr 2027 (M6)'."""
    return f"is already at {w:.0%}" if m == 0 else f"reaches {w:.0%} {_mo(m)}"


def _leads(sc) -> str:
    return " / ".join(f"{'racks' if r == 'space' else r} {sc.lead_time[r]}" for r in C.RESOURCES) + " months"


def _proj_name(sid: str, res: str) -> str:
    return f"{sid} {_res(res)}"


def _at_least(status: str, floor: str = "Medium") -> str:
    order = C.RISK_LEVELS.index
    return status if order(status) >= order(floor) else floor


def decision_insights(da, mc_site: pd.DataFrame | None = None) -> dict[str, list[Insight]]:
    """Executive recommendations grouped by SOW Module 6 category.

    `da` is a dcsim.decisions.DecisionAnalysis. `mc_site` (optional) is
    MCResult.site_summary() indexed by site_id, for probability context.
    """
    plan, b, a = da.plan, da.before, da.after
    sc = plan.scenario
    w = sc.warning_threshold
    p = plan.projects
    ss_b = b.site_summary.set_index("site_id")
    ss_a = a.site_summary.set_index("site_id")
    sr_b = b.site_resource.set_index(["site_id", "resource"])
    names = ss_b["site_name"]
    budgeted = plan.budget_usd is not None
    out: dict[str, list[Insight]] = {k: [] for k in CATEGORIES}
    commit_basis = (f"Commit-by = need month (utilization reaches {w:.0%}) − lead time ({_leads(sc)}). "
                    "Zero or negative means the order should already be placed.")

    # -- 1. Sites requiring immediate attention ------------------------------------
    now = p[p["commit_by_month"] <= 0] if not p.empty else p
    for sid, g in now.groupby("site_id", sort=False):
        parts = [f"{_res(r.resource)} +{fmt_amount(r.capacity_added, r.resource)} "
                 f"({_usd(r.cost_usd)}, live {period_label(r.activation_month)}{'' if r.funded else ', NOT funded'})"
                 for r in g.itertuples(index=False)]
        detail = f"Past the commit-by date: {'; '.join(parts)}."
        for gap in da.gaps[da.gaps["site_id"] == sid].itertuples(index=False):
            detail += (f" Even with the {'funded ' if budgeted else ''}plan, {_res(gap.resource)} runs over capacity "
                       f"for {gap.months_over_capacity} month(s) ({period_label(gap.first_month)}–"
                       f"{period_label(gap.last_month)}), short by up to {fmt_amount(gap.peak_shortfall, gap.resource)}.")
        n_unf = int((~g["funded"].astype(bool)).sum())
        action = "Approve and place orders this month"
        action += f"; {n_unf} of these exceed the capital budget (see Over budget)." if n_unf else "."
        out["immediate"].append(Insight(
            severity=_at_least(ss_b.loc[sid, "risk_status"]),
            title=f"{names[sid]} ({sid}): commit {len(g)} project(s) now · {_usd(g['cost_usd'].sum())}",
            detail=detail, action=action, basis=commit_basis, site_id=sid,
        ))
    if not out["immediate"]:
        nxt = p.sort_values("commit_by_month").iloc[0] if not p.empty else None
        out["immediate"].append(Insight(
            severity="Info", title="No expansion needs to be committed this month",
            detail=(f"The next commit-by date is {_mo(nxt.commit_by_month)} for {_proj_name(nxt.site_id, nxt.resource)}."
                    if nxt is not None else f"No site needs new capacity to stay under {w:.0%} within the horizon."),
            action=None, basis=commit_basis,
        ))

    # -- 2. Facilities at risk of reaching capacity limits -------------------------
    for sid, r in ss_b.iterrows():
        if r["risk_status"] == "Low":
            continue
        tl = []
        for res in C.RESOURCES:
            x = sr_b.loc[(sid, res)]
            bits = []
            if pd.notna(x.warning_month):
                bits.append(f"{w:.0%} " + ("now" if x.warning_month == 0 else period_label(x.warning_month)))
            if pd.notna(x.critical_month):
                bits.append(f"{sc.critical_threshold:.0%} " + ("now" if x.critical_month == 0 else period_label(x.critical_month)))
            if pd.notna(x.exhaustion_month):
                bits.append(f"exhausted {period_label(x.exhaustion_month)}")
            if bits:
                tl.append(f"{_res(res)}: {', '.join(bits)}")
        detail = "; ".join(tl) + f". Rule: {r['risk_reason']}."
        if mc_site is not None and sid in mc_site.index:
            prob = float(mc_site.loc[sid, "prob_within_36m"])
            safe = pd.isna(r["earliest_exhaustion_month"])
            detail += (f" Monte Carlo: {prob:.0%} chance of exhausting a resource within 36 months"
                       + (", although the single-path forecast never does." if safe and prob > 0 else "."))
        after = ss_a.loc[sid, "risk_status"]
        label = "funded plan" if budgeted else "recommended plan"
        action = (f"With the {label} it falls to {after} risk."
                  if C.RISK_LEVELS.index(after) < C.RISK_LEVELS.index(r["risk_status"])
                  else f"It stays {after} even with the {label}: {ss_a.loc[sid, 'risk_reason']}.")
        out["at_risk"].append(Insight(
            severity=r["risk_status"],
            title=f"{names[sid]} ({sid}) · {r['risk_status']} risk · {_res(r['limiting_resource'])} fills first",
            detail=detail, action=action,
            basis="Risk rules on the forecast before new projects (Methodology §5); status after = same rules on the "
                  "post-investment forecast.",
            site_id=sid,
        ))
    if not out["at_risk"]:
        out["at_risk"].append(Insight(
            severity="Low", title="Every facility is Low risk",
            detail=f"No resource runs out within 36 months and none reaches {w:.0%} within {C.RISK_WARNING_WINDOW} months.",
            action=None, basis="Risk rules (Methodology §5).",
        ))

    # -- 3. Recommended expansion timing ----------------------------------------------
    crash_basis = (f"Crashed lead time = ceil(lead × (1 − {C.CRASH_COMPRESSION:.2f})); the {C.CRASH_COMPRESSION:.0%} "
                   "compression is an illustrative assumption, not a vendor quote.")
    for r in da.crash[da.crash["gap_months_on_time"] > 0].itertuples(index=False):
        out["timing"].append(Insight(
            severity="High",
            title=f"{_proj_name(r.site_id, r.resource)} expansion lands {r.gap_months_on_time} month(s) after capacity runs out",
            detail=(f"{_res(r.resource).capitalize()} {_reaches(w, r.need_month)} and runs out {_mo(r.exhaustion_month)}. "
                    f"With a {r.lead_time_months}-month lead time, a project committed today goes live "
                    f"{_mo(r.activation_month)}: demand exceeds capacity for {r.gap_months_on_time} month(s)."),
            action=(f"Crash the schedule to go live {_mo(r.crashed_activation_month)}: the gap shrinks to "
                    f"{r.gap_months_crashed} month(s). Bridge the rest with load placement (see Optimization) "
                    "or temporary on-site generation."),
            basis=crash_basis, site_id=r.site_id,
        ))
    late_ok = da.crash[da.crash["gap_months_on_time"] == 0]
    if len(late_ok):
        items = ", ".join(f"{_proj_name(r.site_id, r.resource)} (live {period_label(r.activation_month)})"
                          for r in late_ok.itertuples(index=False))
        out["timing"].append(Insight(
            severity="Medium",
            title=f"{len(late_ok)} project(s) land after the {w:.0%} line but before capacity runs out",
            detail=f"{items}. These sites run above the planning threshold, not over capacity, until the projects land.",
            action="Commit now; no bridging needed if they hold their lead times.", basis=commit_basis,
        ))
    upcoming = p[p["commit_by_month"] > 0].sort_values("commit_by_month") if not p.empty else p
    for r in upcoming.head(4).itertuples(index=False):
        out["timing"].append(Insight(
            severity="Medium" if r.commit_by_month <= 6 else "Info",
            title=f"Commit {_proj_name(r.site_id, r.resource)} by {period_label(r.commit_by_month)}",
            detail=(f"+{fmt_amount(r.capacity_added, r.resource)} ({_usd(r.cost_usd)}) is needed {_mo(r.need_month)}, "
                    f"when {_res(r.resource)} reaches {w:.0%}; a {r.lead_time_months}-month lead time puts it live "
                    f"{_mo(r.activation_month)}."),
            action="Start design and procurement by then; re-confirm demand at a stage gate before committing.",
            basis=commit_basis, site_id=r.site_id,
        ))
    if len(upcoming) > 4:
        out["timing"].append(Insight(
            severity="Info", title=f"{len(upcoming) - 4} more commit-by date(s) later in the horizon",
            detail="See the expansion timing chart and the investment ranking table.", action=None, basis=commit_basis,
        ))
    if not out["timing"]:
        out["timing"].append(Insight(severity="Info", title="No expansion timing decisions in this horizon",
                                     detail="No project is needed.", action=None, basis=commit_basis))

    # -- 4. Opportunities for capacity optimization ------------------------------------
    unfunded = plan.unfunded if not p.empty else p
    for sid, g in da.deferrable.groupby("site_id", sort=False):
        total, pv = g["cost_usd"].sum(), g["pv_value_defer_12m"].sum()
        peaks = ", ".join(f"{_res(r.resource)} peaks at {r.peak_utilization_without:.0%}" for r in g.itertuples(index=False))
        action = (f"Defer 12 months and re-test at the next stage gate: worth ≈{_usd(pv)} in present value at an "
                  f"illustrative {C.DISCOUNT_RATE:.0%} discount rate.")
        if budgeted and len(unfunded):
            fits = unfunded[unfunded["cost_usd"].cumsum() <= total]
            first = unfunded.iloc[0]
            if len(fits):
                action += f" Re-allocated, {_usd(total)} would fund {len(fits)} of the {len(unfunded)} over-budget project(s)"
            else:
                action += f" Re-allocated, it would cover {total / first.cost_usd:.0%} of the first over-budget project"
            action += f", starting with #{first['rank']} {_proj_name(first.site_id, first.resource)} ({_usd(first.cost_usd)})."
        out["optimization"].append(Insight(
            severity="Info",
            title=f"{names[sid]} ({sid}): defer {_usd(total)} of planned expansion, not needed within 36 months",
            detail=(f"Without it, {peaks}, all under the {w:.0%} warning line through {period_label(C.MAX_HORIZON)}. "
                    f"Scheduled for {period_label(int(g['activation_month'].min()))}: " + "; ".join(g["description"]) + "."),
            action=action,
            basis="Forecast re-run without each planned expansion over the 36-month lookahead. Discounting appears only "
                  "in this note, never in the ranking.",
            site_id=sid,
        ))
    for r in da.pue.itertuples(index=False):
        months = "" if r.months_of_growth == float("inf") else f" (about {r.months_of_growth:.0f} months of power growth)"
        rel = ""
        if r.limiting_resource == "cooling":
            rel = (f" It does not add cooling plant capacity (the IT heat is unchanged), so it does not relieve "
                   f"{r.site_id}'s cooling limit.")
        elif r.limiting_resource != "power":
            rel = (f" Power is not {r.site_id}'s first limit ({_res(r.limiting_resource)} is), so the gain is mostly "
                   "energy and carbon.")
        out["optimization"].append(Insight(
            severity="Info",
            title=f"{r.site_name} ({r.site_id}): PUE {r.pue:.2f} → {r.target_pue:.2f} frees ≈{r.freed_mw:.1f} MW",
            detail=(f"Same IT load, less overhead: {r.freed_mw:.1f} MW of electrical capacity{months} without new utility "
                    f"service, ≈{_usd(r.avoided_capex_usd)} of avoided electrical expansion and "
                    f"≈{r.emissions_saved_tco2e:,.0f} tCO₂e a year.{rel}"),
            action="Containment, warmer supply-air setpoints and more economizer hours.",
            basis=(f"freed MW = facility MW × (1 − target PUE / PUE); target {C.PUE_TARGET:.2f} is an illustrative "
                   f"benchmark for sites at PUE ≥ {C.HIGH_PUE:.2f}."),
            site_id=r.site_id,
        ))
    for r in da.stranded.itertuples(index=False):
        out["optimization"].append(Insight(
            severity="Info",
            title=f"{r.site_name} ({r.site_id}): {r.power_headroom_mw:.1f} MW of stranded power behind full racks",
            detail=(f"Racks are {r.space_utilization:.0%} occupied while power is only {r.power_utilization:.0%} used. "
                    "The site is space-bound, so its electrical headroom cannot be sold."),
            action="Consolidate onto denser servers (more kW per rack) to put the stranded MW to work before building white space.",
            basis="Sites whose first limiting resource is rack space while power is below the warning threshold.",
            site_id=r.site_id,
        ))
    for src, g in da.migration.groupby("source_id", sort=False):
        best = g.iloc[0]
        alt = g.iloc[1] if len(g) > 1 else None
        cover = "covers the whole gap" if best.covers_need else "covers part of it"
        ratio = f"{best.intensity_ratio:.0f}×" if best.intensity_ratio >= 2 else f"{best.intensity_ratio:.1f}×"
        detail = (f"After the {'funded ' if budgeted else ''}plan, {src} is still over capacity "
                  f"{period_label(best.window_start)}–{period_label(best.window_end)}, by up to {best.need_facility_mw:.1f} MW "
                  f"({best.need_it_mw:.1f} MW of IT load). {best.dest_name} ({best.dest_id}) can take "
                  f"{best.dest_absorb_it_mw:.1f} MW of IT through that window and stay under {w:.0%}; moving "
                  f"{best.moved_it_mw:.1f} MW {cover}. Per MWh of IT, {best.dest_id}'s grid and PUE emit {ratio} less "
                  f"(≈{best.emissions_saved_tco2e:,.0f} tCO₂e a year avoided while the load runs there).")
        if alt is not None:
            detail += f" Alternative: {alt.dest_name} ({alt.dest_id}), {alt.dest_absorb_it_mw:.1f} MW of IT."
        out["optimization"].append(Insight(
            severity="Medium",
            title=f"Bridge {names[src]} ({src}) by placing flexible load at {best.dest_name} ({best.dest_id})",
            detail=detail,
            action=(f"Steer new latency-tolerant deployments (AI training) to {best.dest_id} until {src}'s expansion is live; "
                    "confirm backhaul capacity and latency first."),
            basis=("Headroom to the warning threshold on power and cooling over the whole gap window. Emissions = IT MWh × "
                   "PUE × average location-based grid intensity; marginal rates may narrow the gap. Network capacity is "
                   "not modeled."),
            site_id=src,
        ))
    if not out["optimization"]:
        out["optimization"].append(Insight(
            severity="Info", title="No optimization opportunity flagged under these assumptions",
            detail="No deferrable planned spend, no site above the PUE benchmark, no stranded power and no residual gap to bridge.",
            action=None, basis="Optimization rules (Methodology §10).",
        ))

    # -- 5. Investments exceeding the available budget ------------------------------------
    budget_basis = ("Projects are funded in priority order while they fit in the remaining budget; a cheaper lower-ranked "
                    "project can still be funded after a larger one is skipped (Methodology §6).")
    if p.empty:
        out["budget"].append(Insight(severity="Info", title="No new projects proposed", detail="Nothing to fund.",
                                     action=None, basis=budget_basis))
    elif not budgeted:
        out["budget"].append(Insight(
            severity="Info", title="No capital budget applied: every recommended project is funded in this view",
            detail=(f"{len(p)} projects, {_usd(plan.proposed_total)} in total. Already-planned expansions add "
                    f"{_usd(plan.committed_planned_total)} of committed capex outside this figure."),
            action="Turn on a capital budget above (or pick Budget-Constrained in the sidebar) to see the trade-offs.",
            basis=budget_basis,
        ))
    else:
        left = plan.budget_usd - plan.funded_total
        out["budget"].append(Insight(
            severity="Info" if unfunded.empty else "Medium",
            title=(f"{_usd(plan.funded_total)} of the {_usd(plan.budget_usd)} budget funds "
                   f"{int(p['funded'].sum())} of {len(p)} projects"),
            detail=(f"{len(unfunded)} project(s) totalling {_usd(unfunded['cost_usd'].sum())} exceed it; "
                    f"{_usd(left)} is left unallocated."),
            action=None, basis=budget_basis,
        ))
        for r in unfunded.itertuples(index=False):
            ex = r.exhaustion_if_not_built
            if pd.notna(ex):
                sev = "High" if ex <= C.RISK_HIGH_MONTHS else "Medium"
                cons = (f"Without it, {_res(r.resource)} at {r.site_id} runs out {_mo(ex)} and is over capacity for "
                        f"{r.months_over_capacity_if_not_built} of the next 36 months.")
            else:
                sev = "Info"
                cons = (f"Without it, {_res(r.resource)} at {r.site_id} stays above {w:.0%} from "
                        f"{'now' if r.need_month == 0 else _mo(r.need_month)} "
                        "but does not run out within 36 months.")
            defer_hint = " defer the planned spend flagged under Optimization," if len(da.deferrable) else ""
            out["budget"].append(Insight(
                severity=sev,
                title=(f"#{r.rank} {_proj_name(r.site_id, r.resource)} +{fmt_amount(r.capacity_added, r.resource)} "
                       f"({_usd(r.cost_usd)}) exceeds the remaining budget"),
                detail=cons,
                action=(f"It needs {_usd(r.cost_usd - left)} more than the {_usd(left)} left. Raise the budget,{defer_hint} "
                        "or bridge with load placement."),
                basis=budget_basis, site_id=r.site_id,
            ))

    # -- 6. Consequences of deferring -----------------------------------------------------
    d = da.deferral
    defer_basis = (f"Same forecast with only that item's activation moved {da.delay} months later; 36-month lookahead, "
                   "before new recommended projects.")
    if not d.empty:
        harm = d[d["extra_months_over_capacity"] > 0]
        for r in harm.head(4).itertuples(index=False):
            verb = "slips" if r.kind == "Planned" else "is deferred"
            first = ""
            if r.exhaustion_deferred is not None and r.exhaustion_deferred != r.exhaustion_on_time:
                first = f" Capacity first runs out {_mo(r.exhaustion_deferred)}."
            out["deferral"].append(Insight(
                severity="High" if r.extra_months_over_capacity >= 3 else "Medium",
                title=f"If {r.item} {verb} {da.delay} months: +{r.extra_months_over_capacity} month(s) over capacity",
                detail=(f"On schedule ({_mo(r.activation_month)}), {r.site_id} {_res(r.resource)} is over capacity for "
                        f"{r.months_over_capacity_on_time} month(s) of the 36-month view; deferred to "
                        f"{_mo(r.deferred_activation_month)} that becomes {r.months_over_capacity_deferred}, short by up to "
                        f"{fmt_amount(r.peak_shortfall_deferred, r.resource)}.{first}"),
                action="Hold the date: protect long-lead equipment and the utility study, or line up bridging capacity first.",
                basis=defer_basis, site_id=r.site_id,
            ))
        safe = d[d["extra_months_over_capacity"] == 0]
        if len(safe):
            sample = ", ".join(safe["item"].head(6)) + ("…" if len(safe) > 6 else "")
            out["deferral"].append(Insight(
                severity="Info",
                title=f"{len(safe)} other item(s) can slip {da.delay} months without exceeding capacity",
                detail=(f"They would run above the {w:.0%} planning line for up to "
                        f"{int(safe['extra_months_above_warning'].max())} more month(s): {sample}."),
                action=None, basis=defer_basis,
            ))
    if not out["deferral"]:
        out["deferral"].append(Insight(severity="Info", title="Nothing scheduled to defer",
                                       detail="No recommended or planned projects.", action=None, basis=defer_basis))

    return {k: sorted(v, key=lambda x: SEVERITY_ORDER.get(x.severity, 9)) for k, v in out.items()}


def decision_headline(da) -> tuple[str, str, str, str]:
    """(eyebrow, headline, detail, status) for the Executive Decisions hero."""
    plan, b = da.plan, da.before
    p, h = plan.projects, plan.scenario.horizon
    status = b.kpis["portfolio_risk"]
    if p.empty:
        return ("Capital decision", f"No new capacity is needed within the {h}-month horizon", "", "Low")
    now = p[p["commit_by_month"] <= 0]
    detail = []
    gaps = da.crash[da.crash["gap_months_on_time"] > 0].sort_values("gap_months_on_time", ascending=False)
    if len(gaps):
        g = gaps.iloc[0]
        detail.append(f"The {_proj_name(g.site_id, g.resource)} project still lands {g.gap_months_on_time} month(s) after "
                      f"capacity runs out ({period_label(g.exhaustion_month)}); crashing narrows that to "
                      f"{g.gap_months_crashed} and load placement can cover the rest.")
    if plan.budget_usd is not None:
        n_unf = len(plan.unfunded)
        detail.append(f"The {_usd(plan.budget_usd)} budget funds {len(p) - n_unf} of {len(p)} projects"
                      + (f"; {n_unf} exceed it." if n_unf else "."))
    if len(da.deferrable):
        detail.append(f"{_usd(da.deferrable['cost_usd'].sum())} of planned expansion is not needed within 36 months.")
    if len(now):
        head = f"Commit {len(now)} expansion project(s) now: {_usd(now['cost_usd'].sum())} already past the commit-by date"
        return ("Capital decision this month", head, " ".join(detail), status)
    nxt = p.sort_values("commit_by_month").iloc[0]
    head = f"Next commitment: {_proj_name(nxt.site_id, nxt.resource)} by {period_label(nxt.commit_by_month)}"
    return ("Capital decision", head, " ".join(detail), _at_least(status))


def mc_insights(mc, mc_no_slip=None, limit: int = 4) -> list[Insight]:
    """Where the Monte Carlo view disagrees with the single-path forecast."""
    es = mc.exhaustion_summary()
    noslip = mc_no_slip.exhaustion_summary().set_index(["site_id", "resource"]) if mc_no_slip is not None else None
    s = mc.settings
    basis = (f"{s.n_draws:,} simulated futures: growth ±{s.growth_sd * 100:.0f} pts (correlation {s.growth_corr:.2f}), "
             f"schedule slip Triangular(0, {s.slip_mode:g}, {s.slip_max:g}) months, efficiency realised "
             f"{s.eff_realization_low:.0%}–100%.")
    scored = []
    for r in es.itertuples(index=False):
        det, p10, prob = r.deterministic_month, r.p10_month, r.prob_within_36m
        driver = ""
        if noslip is not None:
            n = noslip.loc[(r.site_id, r.resource)]
            p24, p24_0, p36_0 = float(r.prob_within_24m), float(n["prob_within_24m"]), float(n["prob_within_36m"])
            if p24 - p24_0 >= 0.05:
                driver = (f" Over the next 24 months the risk is schedule slip: {p24:.0%} as simulated vs {p24_0:.0%} "
                          "if planned expansions land on time.")
                if p36_0 >= 0.05:
                    driver += f" The later risk ({p36_0:.0%} even with no slip) is demand outgrowing the new capacity."
            elif prob - p36_0 >= 0.05:
                driver = f" Schedule slip adds {prob - p36_0:.0%} points; the rest comes from demand growth."
            else:
                driver = " Schedule slip barely matters here; the spread comes from demand growth."
        if pd.isna(det) and prob >= 0.15:
            scored.append((prob, Insight(
                severity="High" if prob >= 0.5 else "Medium",
                title=(f"{r.site_name} ({r.site_id}) {_res(r.resource)}: the forecast says it never runs out; "
                       f"{prob:.0%} of futures say it does"),
                detail=f"10% of simulated futures exhaust it by {_mo(p10)}.{driver}",
                action="Plan on the cautious tail: use the P10 date as the need date for commit-by decisions.",
                basis=basis, site_id=r.site_id,
            )))
        elif pd.notna(det) and pd.notna(p10) and det - p10 >= 6:
            p50 = f"month {r.p50_month}" if pd.notna(r.p50_month) else "beyond month 36"
            scored.append((1 + (det - p10) / 36, Insight(
                severity="Medium",
                title=f"{r.site_name} ({r.site_id}) {_res(r.resource)}: plan on {period_label(p10)}, not {period_label(det)}",
                detail=(f"The single-path forecast runs out in month {det}; 10% of futures run out by month {p10} and the "
                        f"median is {p50}. Slow, steady growth makes the date fragile: a few points of extra growth move "
                        f"it by years.{driver}"),
                action=None, basis=basis, site_id=r.site_id,
            )))
    scored.sort(key=lambda t: -t[0])
    return [i for _, i in scored[:limit]]


def briefing(da) -> list[tuple[str, str]]:
    """'How to say it in a meeting' (Guide §19), filled from the live forecast."""
    from .sustainability import carbon_contrast, footprint

    b, plan = da.before, da.plan
    sc, k, p = plan.scenario, b.kpis, plan.projects
    s = b.sites.set_index("site_id")
    ss = b.site_summary.set_index("site_id")
    out = [("In one sentence",
            "It forecasts when each of six data centers runs out of power, cooling or space, tells you what to build "
            "and when to order it, and puts odds on the dates so you can see schedule risk, not just a single guess.")]

    # Operations director
    ec = k["earliest_constraint"]
    txt = ""
    if ec:
        res, j = ec["resource"], C.RESOURCES.index(ec["resource"])
        pu = b.demand[:, j, 0].sum() / b.capacity[:, j, 0].sum()
        lead = f"Portfolio {_res(res)} utilization is {pu:.0%}, which sounds fine, but {ec['site_name']}"
        if ec["kind"] == "exhaustion":
            txt = f"{lead} runs out of {_res(res)} in {ec['period']}"
            proj = p[(p["site_id"] == ec["site_id"]) & (p["resource"] == res)] if not p.empty else p
            if len(proj):
                r = proj.iloc[0]
                if r.activation_month > ec["month"]:
                    art = "an" if _res(res)[0] in "aeiou" else "a"
                    txt += (f", and {art} {_res(res)} project ordered today won't land until {period_label(r.activation_month)}. "
                            f"That's a {int(r.activation_month - ec['month'])}-month gap to cover.")
                else:
                    when = "today" if r.commit_by_month <= 0 else f"by {period_label(r.commit_by_month)}"
                    txt += f". A project committed {when} lands in time."
            else:
                txt += "."
        else:
            txt = (f"{lead} reaches the {sc.warning_threshold:.0%} {_res(res)} line in {ec['period']}. "
                   "No site runs out within 36 months.")
    low = ss[ss["risk_status"] == "Low"].index
    if len(low):
        head = {sid: float(b.capacity[b.site_ids.index(sid), 0, 0] - b.demand[b.site_ids.index(sid), 0, 0]) for sid in low}
        idle = max(head, key=head.get)
        txt += f" Meanwhile {s.loc[idle, 'site_name']} has {head[idle]:.0f} MW idle"
        dfr = da.deferrable[da.deferrable["site_id"] == idle]
        txt += f" and {_usd(dfr['cost_usd'].sum())} of planned expansion we could defer." if len(dfr) else "."
    if txt:
        out.append(("For an operations director", txt.strip()))

    # Utility / grid planner
    pw = p[p["resource"] == "power"].sort_values("need_month") if not p.empty else p
    if len(pw):
        r = pw.iloc[0]
        site = s.loc[r.site_id]
        dirtiest = site["grid_carbon_t_per_mwh"] >= s["grid_carbon_t_per_mwh"].max()
        grid = "the portfolio's most carbon-intensive grid" if dirtiest else "its grid"
        txt = (f"{site['site_name']} is a {site['power_capacity_mw']:.0f} MW load on {grid} "
               f"({site['grid_carbon_t_per_mwh']:.2f} tCO₂e/MWh), growing {ss.loc[r.site_id, 'annual_growth']:.0%} a year. "
               f"We need about {r.capacity_added:.1f} MW more to stay under {sc.warning_threshold:.0%} through "
               f"{period_label(sc.horizon)}")
        if r.commit_by_month <= 0:
            txt += (", so we'd like to start a large-load interconnection study now; even ordered today it lands "
                    f"{period_label(r.activation_month)}")
        else:
            txt += f", so the interconnection request has to be in by {period_label(r.commit_by_month)}"
        mig = da.migration[da.migration["source_id"] == r.site_id]
        if len(mig):
            txt += f". We're also looking at shifting flexible training load to {' or '.join(mig['dest_name'])} while it's built"
        out.append(("For a utility or grid planner", txt + "."))

    # Sustainability lead
    txt = ""
    if len(da.migration):
        m = da.migration.iloc[0]
        ratio = f"{m.intensity_ratio:.0f}×" if m.intensity_ratio >= 2 else f"{m.intensity_ratio:.1f}×"
        txt = (f"Moving flexible training load from {m.source_name} to {m.dest_name} cuts its emissions per MWh of IT "
               f"about {ratio} and buys time for the expansion.")
    else:
        cc = carbon_contrast(b)
        if cc:
            txt = (f"{cc['dirty']['site_name']} draws {cc['power_ratio'] - 1:.0%} more power than {cc['clean']['site_name']} "
                   f"but emits about {cc['emissions_ratio']:.0f}× the CO₂: where load lands matters more than how "
                   "efficiently it runs.")
    fp = footprint(b, 0).set_index("site_id")
    wr = fp.loc[fp["wue_l_per_kwh"].idxmax()]
    txt += (f" {wr.site_name} is our water-risk site: {wr.wue_l_per_kwh:.1f} L/kWh, about {wr.annual_water_ml:,.0f} million "
            "litres a year" + (", which is nearly fully evaporative." if wr.wue_l_per_kwh >= 1.5 else "."))
    txt += (f" Portfolio location-based Scope 2 is about {fp['annual_emissions_tco2e'].sum() / 1000:,.0f} kt CO₂e a year "
            "at today's draw.")
    out.append(("For a sustainability lead", txt.strip()))
    return out
