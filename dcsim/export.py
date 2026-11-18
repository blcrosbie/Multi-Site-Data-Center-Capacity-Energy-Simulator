"""Downloadable reports (SOW Module 7).

Every table is built from the same ForecastResult / CapitalPlan the pages show,
so a download always matches the screen. Month 0 rows are labelled
"Actual (synthetic baseline)"; later months "Projected".
"""

from __future__ import annotations

import pandas as pd

from . import config as C
from .capital import CapitalPlan
from .forecast import ForecastResult, period_label

NOTICE = "Synthetic data - demonstration only. All facilities, names and values are fictional."


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """UTF-8 with BOM so Excel shows symbols (tCO2e, em dashes) correctly."""
    return df.to_csv(index=False).encode("utf-8-sig")


def facility_capacity(result: ForecastResult) -> pd.DataFrame:
    """One row per site and resource: today, horizon, thresholds, exhaustion, risk."""
    sr = result.site_resource.copy()
    ss = result.site_summary.set_index("site_id")
    sites = result.sites.set_index("site_id")
    sr.insert(2, "region", sr["site_id"].map(ss["region"]))
    sr["annual_growth_pct"] = sr["site_id"].map(ss["annual_growth"]) * 100
    sr["pue"] = sr["site_id"].map(sites["pue"])
    sr["first_limiting_resource"] = sr["site_id"].map(ss["limiting_resource"]) == sr["resource"]
    sr["risk_status"] = sr["site_id"].map(ss["risk_status"])
    sr["risk_reason"] = sr["site_id"].map(ss["risk_reason"])
    for c in ("warning_month", "critical_month"):
        sr[c.replace("_month", "_period")] = [period_label(m) if pd.notna(m) else None for m in sr[c]]
    sr.insert(0, "scenario", result.scenario.name)
    sr.insert(1, "horizon_months", result.horizon)
    return sr


def monthly_forecast(result: ForecastResult) -> pd.DataFrame:
    """Monthly demand, capacity, headroom and utilization per site and resource through the horizon."""
    sc = result.scenario
    m = result.monthly.copy()
    m["above_warning"] = m["utilization"] >= sc.warning_threshold
    m["above_critical"] = m["utilization"] >= sc.critical_threshold
    m["over_capacity"] = m["demand"] > m["capacity"]
    m.insert(0, "scenario", sc.name)
    return m


def scenario_assumptions(result: ForecastResult) -> list[tuple[str, str, str, object, str]]:
    sc = result.scenario
    rows = [
        ("assumption", "", "scenario", sc.name, ""),
        ("assumption", "", "baseline_period", C.AS_OF_PERIOD.strftime("%Y-%m"), "actual"),
        ("assumption", "", "horizon", sc.horizon, "months"),
        ("assumption", "", "warning_threshold", sc.warning_threshold, "fraction"),
        ("assumption", "", "critical_threshold", sc.critical_threshold, "fraction"),
        ("assumption", "", "portfolio_growth_override", sc.portfolio_growth, "fraction / yr"),
        ("assumption", "", "efficiency_improvement", sc.efficiency, "fraction of power + cooling demand"),
        ("assumption", "", "rack_density_growth", sc.density_growth, "fraction / yr"),
        ("assumption", "", "include_planned_expansions", sc.include_planned, ""),
        ("assumption", "", "capital_budget", sc.budget_usd, "USD"),
        ("assumption", "", "cost_per_mw", sc.cost_per_mw, "USD / MW"),
        ("assumption", "", "cost_per_mwt", sc.cost_per_mwt, "USD / MWt"),
        ("assumption", "", "cost_per_rack", sc.cost_per_rack, "USD / rack"),
    ]
    rows += [("assumption", "", f"lead_time_{r}", sc.lead_time[r], "months") for r in C.RESOURCES]
    for sid, g in zip(result.site_ids, result.growth):
        rows.append(("assumption", sid, "annual_growth_applied", float(g), "fraction / yr"))
    for e in result.expansions:
        if e.source != "planned":
            rows.append(("assumption", e.site_id, f"expansion:{e.resource}",
                         f"+{e.amount:g} {C.RESOURCE_UNITS[e.resource]} at month {e.activation_month} ({e.label})", e.source))
    return rows


def scenario_results(result: ForecastResult, plan: CapitalPlan) -> pd.DataFrame:
    """Scenario assumptions and results in one long table: section, site, item, value, unit."""
    k = result.kpis
    rows = scenario_assumptions(result)
    for key in ("power_capacity_mw", "power_demand_mw", "power_utilization", "power_headroom_mw",
                "cooling_capacity_mwt", "cooling_load_mwt", "cooling_utilization", "rack_capacity",
                "racks_occupied", "rack_utilization", "power_demand_12m_mw", "power_demand_24m_mw",
                "power_demand_36m_mw", "sites_approaching_constraint", "portfolio_risk"):
        rows.append(("portfolio_kpi", "", key, k[key], ""))
    ec = k["earliest_constraint"]
    if ec:
        rows.append(("portfolio_kpi", ec["site_id"], f"earliest_{ec['kind'].replace(' ', '_')}",
                     f"{ec['resource']} month {ec['month']} ({ec['period']})", ""))
    rows.append(("portfolio_kpi", "", "portfolio_power_exhaustion_month", k["portfolio_power_exhaustion_month"], "month"))
    for r in result.site_summary.itertuples(index=False):
        rows += [
            ("site_result", r.site_id, "risk_status", r.risk_status, ""),
            ("site_result", r.site_id, "risk_reason", r.risk_reason, ""),
            ("site_result", r.site_id, "first_limiting_resource", r.limiting_resource, r.limiting_basis),
            ("site_result", r.site_id, "earliest_exhaustion_month",
             None if pd.isna(r.earliest_exhaustion_month) else int(r.earliest_exhaustion_month), "month"),
        ]
    rows += [
        ("capital", "", "new_projects_proposed", len(plan.projects), "projects"),
        ("capital", "", "new_projects_cost", plan.proposed_total, "USD"),
        ("capital", "", "new_projects_funded_cost", plan.funded_total, "USD"),
        ("capital", "", "projects_exceeding_budget", len(plan.unfunded) if not plan.projects.empty else 0, "projects"),
        ("capital", "", "committed_planned_capex", plan.committed_planned_total, "USD"),
    ]
    df = pd.DataFrame(rows, columns=["section", "site_id", "item", "value", "unit"])
    df["value"] = df["value"].astype(object).where(df["value"].notna(), "")
    return df


def scenario_comparison(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def capital_comparison(plan: CapitalPlan) -> pd.DataFrame:
    """Capital investment comparison: every sized project with scores, timing, funding and post-investment headroom."""
    p = plan.projects.copy()
    if p.empty:
        return p
    p.insert(0, "scenario", plan.scenario.name)
    p["budget_usd"] = plan.budget_usd
    p["commit_by_period"] = [period_label(m) if m > 0 else "Now (overdue)" if m < 0 else "Now" for m in p["commit_by_month"]]
    return p


def executive_summary_md(ctx_scenario, headline: tuple, insights: dict, titles: dict, plan: CapitalPlan,
                         briefing: list[tuple[str, str]]) -> str:
    """Printable executive summary (Markdown)."""
    sc = ctx_scenario
    eyebrow, head, detail, status = headline
    k = (plan.after if plan.budget_usd is not None else plan.before).kpis
    lines = [
        "# Six-Site Data Center Capacity: Executive Summary",
        "",
        f"> {NOTICE}",
        "",
        f"**Scenario:** {sc.name} · **Horizon:** {sc.horizon} months · **Baseline:** "
        f"{C.AS_OF_PERIOD.strftime('%b %Y')} (actual) · **Thresholds:** {sc.warning_threshold:.0%} / "
        f"{sc.critical_threshold:.0%} · **Budget:** "
        + ("none" if plan.budget_usd is None else f"${plan.budget_usd / 1e6:,.0f}M"),
        "",
        f"## {eyebrow}: {head}",
        "",
        detail,
        "",
        "## Portfolio",
        "",
        f"- Electrical: {k['power_demand_mw']:,.1f} of {k['power_capacity_mw']:,.1f} MW ({k['power_utilization']:.0%})",
        f"- Cooling: {k['cooling_load_mwt']:,.1f} of {k['cooling_capacity_mwt']:,.1f} MWt ({k['cooling_utilization']:.0%})",
        f"- Racks: {k['racks_occupied']:,.0f} of {k['rack_capacity']:,.0f} ({k['rack_utilization']:.0%})",
        f"- Portfolio risk: {k['portfolio_risk']}",
        f"- New projects: {len(plan.projects)} · ${plan.proposed_total / 1e6:,.1f}M proposed · "
        f"${plan.funded_total / 1e6:,.1f}M funded",
        "",
        "## Recommendations",
    ]
    for key, items in insights.items():
        lines += ["", f"### {titles[key]}", ""]
        for i in items:
            who = f"{i.site_id} · " if i.site_id else ""
            lines.append(f"- **[{i.severity}] {who}{i.title}.** {i.detail}" + (f" *Action:* {i.action}" if i.action else ""))
            lines.append(f"  - Based on: {i.basis}")
    if not plan.projects.empty:
        lines += ["", "## Ranked investments", "",
                  "| Rank | Site | Resource | Added | Cost | Commit by | Live | Funded |",
                  "|---:|---|---|---:|---:|---|---|---|"]
        for r in plan.projects.itertuples(index=False):
            commit = "Now" if r.commit_by_month <= 0 else period_label(r.commit_by_month)
            lines.append(f"| {r.rank} | {r.site_id} | {C.RESOURCE_LABELS[r.resource]} | {r.capacity_added:g} {r.unit} | "
                         f"${r.cost_usd / 1e6:,.1f}M | {commit} | {period_label(r.activation_month)} | "
                         f"{'yes' if r.funded else 'no'} |")
    lines += ["", "## Briefing notes", ""]
    lines += [f"**{a}.** {t}\n" for a, t in briefing]
    lines += ["", "*Costs are illustrative order-of-magnitude figures, not engineering estimates. No ROI is claimed.*", ""]
    return "\n".join(lines)
