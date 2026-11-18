"""Executive Decisions - capital ranking vs budget, expansion timing, deferral consequences,
optimization opportunities and the rules-based recommendations panel (SOW Modules 5, 6 & 7)."""

import pandas as pd
import streamlit as st

from dcsim import config as C
from dcsim import export
from dcsim import theme as T
from dcsim.forecast import period_label
from dcsim.rules import CATEGORIES, CATEGORY_HELP, briefing, decision_headline, decision_insights, fmt_amount
from dcsim.validation import ValidationError
from ui import charts, state
from ui import components as ui

ss = st.session_state
ctx = state.context()
plan, sc = ctx.plan, ctx.scenario
p = plan.projects
h, warn = sc.horizon, sc.warning_threshold
if "dec_delay" not in ss:
    ss["dec_delay"] = C.DEFAULT_DEFERRAL_TEST
delay = int(ss["dec_delay"])
da = state.decision_analysis(ctx, delay)
try:
    mc_site = state.monte_carlo(plan.scenario).site_summary().set_index("site_id")
except ValidationError:
    mc_site = None
insights = decision_insights(da, mc_site)
headline = decision_headline(da)
notes = briefing(da)

ui.page_header("Executive Decisions", ctx)
ui.hero(*headline)

# --- KPIs ----------------------------------------------------------------------------------
budgeted = plan.budget_usd is not None
n_now = int((p["commit_by_month"] <= 0).sum()) if not p.empty else 0
proj = lambda n: "project" if n == 1 else "projects"  # noqa: E731
late = da.crash
gap_rows = late[late["gap_months_on_time"] > 0]
ui.kpi_row([
    ui.kpi("Proposed new investment", f"${plan.proposed_total / 1e6:,.1f}", "M",
           f"{len(p)} projects sized to stay ≤ {warn:.0%} through M{h}"),
    ui.kpi("Capital budget", "No cap" if not budgeted else f"${plan.budget_usd / 1e6:,.0f}", "" if not budgeted else "M",
           "All projects treated as funded" if not budgeted
           else f"${plan.funded_total / 1e6:,.1f}M allocated · ${(plan.budget_usd - plan.funded_total) / 1e6:,.1f}M left"),
    ui.kpi("Exceeds budget", f"{len(plan.unfunded) if not p.empty else 0}", proj(len(plan.unfunded) if not p.empty else 0),
           f"${plan.unfunded['cost_usd'].sum() / 1e6:,.1f}M unfunded" if budgeted and not p.empty else "Set a budget to test",
           accent=T.STATUS["serious"] if budgeted and not p.empty and len(plan.unfunded) else None),
    ui.kpi("Committed planned capex", f"${plan.committed_planned_total / 1e6:,.1f}", "M",
           "Already-approved/planned expansions, outside the budget"
           + (f" · ${da.deferrable['cost_usd'].sum() / 1e6:,.1f}M deferrable" if len(da.deferrable) else "")),
])
st.write("")
ui.kpi_row([
    ui.kpi("Commit this month", f"{n_now}", proj(n_now),
           f"${p.loc[p['commit_by_month'] <= 0, 'cost_usd'].sum() / 1e6:,.1f}M past the commit-by date" if n_now else "None overdue",
           accent=T.STATUS["serious"] if n_now else None),
    ui.kpi("Land after capacity runs out", f"{len(gap_rows)}", proj(len(gap_rows)),
           ", ".join(f"{r.site_id} {charts.RES_SHORT[r.resource]}: {r.gap_months_on_time} mo gap" for r in gap_rows.itertuples())
           or "No project lands after its site runs out",
           accent=T.STATUS["critical"] if len(gap_rows) else None),
    ui.kpi("Months over capacity after plan", f"{int(da.gaps['months_over_capacity'].sum()) if len(da.gaps) else 0}",
           "site-months", "; ".join(f"{g.site_id} {charts.RES_SHORT[g.resource]} {g.months_over_capacity}"
                                     for g in da.gaps.itertuples()) or f"None within {h} months"),
    ui.kpi("Portfolio risk after plan", f"{T.RISK_ICONS[da.after.kpis['portfolio_risk']]} {da.after.kpis['portfolio_risk']}", "",
           f"Before new projects: {da.before.kpis['portfolio_risk']}", accent=T.RISK_COLORS[da.after.kpis["portfolio_risk"]]),
])

# --- Budget control (same levers as Scenario Planning) ----------------------------------------------
st.write("")
with st.container(border=True):
    b1, b2, b3 = st.columns([1, 1, 2], vertical_alignment="center")
    b1.toggle("Cap new-project capital", key="budget_on", on_change=state.mark_custom)
    b2.number_input("Capital budget ($M)", 0.0, 5_000.0, step=5.0, key="budget_m", on_change=state.mark_custom,
                    disabled=not ss["budget_on"])
    b3.caption(f"Unit costs ${sc.cost_per_mw / 1e6:,.2f}M/MW · ${sc.cost_per_mwt / 1e6:,.2f}M/MWt · "
               f"${sc.cost_per_rack / 1e3:,.0f}k/rack; lead times power {sc.lead_time['power']} · cooling "
               f"{sc.lead_time['cooling']} · racks {sc.lead_time['space']} months. Illustrative, not engineering estimates.")
    b3.page_link("views/scenarios.py", label="Change costs, lead times and other levers", icon=":material/tune:")

# --- Recommendations panel ------------------------------------------------------------------------
st.subheader("Executive recommendations")
keys = list(CATEGORIES)
tabs = st.tabs([f"{CATEGORIES[k]} ({len(insights[k])})" for k in keys])
for tab, key in zip(tabs, keys):
    with tab:
        ui.section_note(CATEGORY_HELP[key])
        st.write("")
        for ins in insights[key]:
            ui.insight_card(ins)
st.caption("Deterministic, rules-based logic: every recommendation names the metric and rule it rests on. "
           "No AI is involved. Rules: Methodology §5, §6 and §10.")

# --- Capital vs budget + timing --------------------------------------------------------------------
if p.empty:
    st.info(f"No site needs new capacity to stay at or below {warn:.0%} through month {h}.")
else:
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.subheader("Capital budget vs proposed investment")
        st.plotly_chart(charts.capex_ladder(p, plan.budget_usd), **charts.CHART_KW)
        st.caption("Projects in funding order: funded projects first (by rank), then those that do not fit. Each bar spans "
                   "the cumulative spend it adds. " + ("The dashed line is the budget." if budgeted else "No budget cap set."))
    with c2:
        st.subheader("Recommended expansion timing")
        st.plotly_chart(charts.project_timeline(p, h), **charts.CHART_KW)
        st.caption(f"Bar = lead time from commit-by to live. ▲ = utilization reaches {warn:.0%} (need date). "
                   "■ = runs out if not built. ◂ at today = commit-by already passed. A ■ before the bar ends is a gap to bridge.")

    # --- Ranking table ---------------------------------------------------------------------------
    st.subheader("Investment ranking")
    tbl = pd.DataFrame({
        "Rank": p["rank"],
        "Site": p["site_id"] + " · " + p["site_name"],
        "Resource": p["resource"].map(C.RESOURCE_LABELS),
        "Capacity added": [fmt_amount(a, r_) for a, r_ in zip(p["capacity_added"], p["resource"])],
        "Cost ($M)": p["cost_usd"] / 1e6,
        "Funded": p["funded"],
        "Risk": [f"{T.RISK_ICONS[s]} {s}" for s in p["risk_status"]],
        "Need": [period_label(m) for m in p["need_month"]],
        "Commit by": ["Now" if m <= 0 else period_label(m) for m in p["commit_by_month"]],
        "Live": [period_label(m) for m in p["activation_month"]],
        "Late": ["yes" if x else "" for x in p["arrives_late"]],
        "Urgency": p["urgency_score"], "Risk score": p["risk_score"], "Cost score": p["cost_score"],
        "Priority": p["priority_score"],
        f"Headroom M{h} after": [fmt_amount(a, r_) for a, r_ in zip(p["horizon_headroom_after"], p["resource"])],
        f"Util. M{h} after": p["horizon_utilization_after"] * 100,
    })
    st.dataframe(tbl, hide_index=True, row_height=32, height=32 * (len(tbl) + 1) + 3, column_config={
        "Cost ($M)": st.column_config.NumberColumn(format="$%.1f"),
        "Urgency": st.column_config.NumberColumn(format="%.0f"),
        "Risk score": st.column_config.NumberColumn(format="%.0f"),
        "Cost score": st.column_config.NumberColumn(format="%.0f"),
        "Priority": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f", color=T.ACCENT),
        f"Util. M{h} after": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%", color=T.ACCENT),
        "Late": st.column_config.TextColumn(help="Need month is sooner than the lead time allows"),
    })
    with st.expander("How projects are sized, scored and funded"):
        w = C.PRIORITY_WEIGHTS
        st.markdown(f"""
- **Sizing.** The smallest addition (in {C.SIZING_INCREMENT['power']} MW / {C.SIZING_INCREMENT['cooling']} MWt /
  {C.SIZING_INCREMENT['space']}-rack steps) that keeps utilization ≤ {warn:.0%} in every month through M{h}.
- **Timing.** Need = first month at {warn:.0%}; commit-by = need − lead time; live = max(need, lead time).
- **Cost** = capacity × unit cost (illustrative, flat, undiscounted; no ROI claimed).
- **Priority** = {w['urgency']} × urgency + {w['risk']} × risk + {w['cost']} × cost, each 0–100:
  urgency = 100 × (1 − commit-by / {C.URGENCY_SLACK_MONTHS}) clipped; risk = site status (Low 0 · Medium 33 · High 67 ·
  Critical 100); cost = 100 × cheapest / this project.
- **Budget.** Fund in rank order while the project fits in what is left; a cheaper lower-ranked project can still be
  funded after a larger one is skipped. Planned expansions are committed capex outside the budget.

Full method: [Methodology §6]({ui.METHODOLOGY_URL}).
""")

# --- Deferral consequences ---------------------------------------------------------------------------
st.subheader("Consequences of deferring")
st.slider("Test a deferral or slip of (months)", 1, 12, key="dec_delay",
          help="Moves one project at a time later by this many months; everything else stays as planned.")
d = da.deferral
if d.empty:
    st.caption("Nothing to defer.")
else:
    dt = pd.DataFrame({
        "Item": d["item"],
        "Type": d["kind"],
        "Site": d["site_id"],
        "Live on schedule": [period_label(m) for m in d["activation_month"]],
        "Live if deferred": [period_label(m) if m <= C.MAX_HORIZON else "beyond Oct 2029" for m in d["deferred_activation_month"]],
        "Months over capacity (on time)": d["months_over_capacity_on_time"],
        "Months over capacity (deferred)": d["months_over_capacity_deferred"],
        "Extra months over capacity": d["extra_months_over_capacity"],
        f"Extra months above {warn:.0%}": d["extra_months_above_warning"],
        "Peak shortfall": [fmt_amount(v, r_) if v > 0 else "—" for v, r_ in zip(d["peak_shortfall_deferred"], d["resource"])],
    })
    st.dataframe(dt, hide_index=True, height=min(420, 35 * (len(dt) + 1) + 3), column_config={
        "Extra months over capacity": st.column_config.ProgressColumn(min_value=0, max_value=max(12, int(dt["Extra months over capacity"].max())),
                                                                      format="%d", color=T.STATUS["critical"]),
    })
    st.caption("Measured on the forecast before new projects, over the full 36-month lookahead. 'Planned' rows test the "
               "already-approved expansions slipping; 'Recommended' rows test deferring the new projects.")

# --- Briefing notes -------------------------------------------------------------------------------------
st.subheader("Briefing notes")
st.caption("How to say it in a meeting: each note is assembled from the live numbers above, so it changes with the scenario.")
btabs = st.tabs([a for a, _ in notes])
for t, (_, text) in zip(btabs, notes):
    with t:
        ui.brief(text)

# --- Exports ----------------------------------------------------------------------------------------------
st.subheader("Downloads")
slug = sc.name.lower().replace(" ", "_").replace("-", "_")
e1, e2, e3 = st.columns(3)
e1.download_button("Capital investment comparison (CSV)", export.to_csv_bytes(export.capital_comparison(plan)),
                   file_name=f"capital_plan_{slug}.csv", mime="text/csv", on_click="ignore",
                   icon=":material/download:", width="stretch", disabled=p.empty,
                   help="Every sized project: scores, rank, timing, funding and headroom after investment.")
e2.download_button("Deferral analysis (CSV)", export.to_csv_bytes(d), file_name=f"deferral_{delay}mo_{slug}.csv",
                   mime="text/csv", on_click="ignore", icon=":material/download:", width="stretch", disabled=d.empty)
md = export.executive_summary_md(sc, headline, insights, {k: v.split(":")[0] for k, v in CATEGORY_HELP.items()},
                                 plan, notes)
e3.download_button("Executive summary (Markdown)", md.encode("utf-8"), file_name=f"executive_summary_{slug}.md",
                   mime="text/markdown", on_click="ignore", icon=":material/description:", width="stretch",
                   help="Printable summary: headline, KPIs, every recommendation with its basis, ranked investments, briefing notes.")
