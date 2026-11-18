"""Scenario Planning - every what-if lever, impact vs baseline, the five presets side by side,
and the Monte Carlo risk layer (SOW Modules 4 & 7, plus the Monte Carlo extension)."""

import pandas as pd
import streamlit as st

from dcsim import config as C
from dcsim import export
from dcsim import theme as T
from dcsim.forecast import period_label
from dcsim.rules import mc_insights
from dcsim.validation import ValidationError
from ui import charts, state
from ui import components as ui

ss = st.session_state
ctx = state.context()
r, sc = ctx.result, ctx.scenario
h, warn = sc.horizon, sc.warning_threshold
ids = r.site_ids
names = r.site_summary.set_index("site_id")["site_name"]
base = state.displayed(state.preset_plan("Baseline"))

ui.page_header("Scenario Planning", ctx)
st.caption("Pull any lever: every page (KPIs, charts, risk status, capital plan and recommendations) recalculates "
           "immediately. Sidebar buttons load a predefined scenario; editing a lever here turns it into a Custom scenario.")

left, right = st.columns([2, 3], gap="large")

# ============================== Levers ==============================
with left:
    with st.container(border=True):
        st.markdown("**Demand growth**")
        st.toggle("Portfolio-wide growth override", key="pg_on", on_change=state.mark_custom, disabled=ss["sg_on"],
                  help="Replace every site's growth assumption with one rate (the High Growth preset uses 25%).")
        st.slider("Annual growth, all sites (%)", -50.0, 100.0, step=0.5, key="pg_pct", on_change=state.mark_custom,
                  disabled=not ss["pg_on"] or ss["sg_on"])
        st.toggle("Site-specific growth", key="sg_on", on_change=state.toggle_site_growth,
                  help="Set each site's annual growth individually. Overrides the portfolio-wide rate.")
        if ss["sg_on"]:
            cols = st.columns(3)
            for k, sid in enumerate(ids):
                cols[k % 3].number_input(f"{sid} (%/yr)", -50.0, 100.0, step=0.5, format="%.1f", key=f"sg_{sid}",
                                         on_change=state.mark_custom)
        else:
            data_g = " · ".join(f"{sid} {g:.1f}%" for sid, g in zip(ids, ctx.sites["annual_growth_pct"]))
            st.caption(f"Data assumptions: {data_g}")

    with st.container(border=True):
        st.markdown("**Operational efficiency**")
        st.slider("Efficiency improvement (%)", 0, 50, key="eff_pct", on_change=state.mark_custom,
                  help="Reduces modeled power and cooling demand from month 1 (the Efficiency Optimization preset uses 10%). "
                       "Optimistic by design: real PUE gains cut overhead, not IT load.")

    with st.container(border=True):
        st.markdown("**Infrastructure expansion**")
        n_planned = len(ctx.planned)
        st.toggle(f"Include planned expansions ({n_planned} projects, ${ctx.planned['cost_usd'].sum() / 1e6:,.1f}M)",
                  key="include_planned", on_change=state.mark_custom)
        st.selectbox("Add capacity at", ids, key="exp_site", on_change=state.mark_custom,
                     format_func=lambda s: f"{s} · {names[s]}")
        a, b, c = st.columns(3)
        a.number_input("Power (MW)", 0.0, 500.0, step=0.5, key="exp_power", on_change=state.mark_custom)
        b.number_input("Cooling (MWt)", 0.0, 500.0, step=0.5, key="exp_cooling", on_change=state.mark_custom)
        c.number_input("Racks", 0, 10_000, step=10, key="exp_racks", on_change=state.mark_custom)
        st.slider("Activation month", 1, C.MAX_HORIZON, key="exp_month", on_change=state.mark_custom,
                  help="Forecast month the added capacity becomes usable.")
        scen_cost = sum(e.cost_usd or 0 for e in r.expansions if e.source == "scenario")
        st.caption(f"Goes live {period_label(ss['exp_month'])} · estimated ${scen_cost / 1e6:,.1f}M at current unit costs.")
        st.button("Size it for me", on_click=state.autosize_expansion, icon=":material/straighten:",
                  help="Smallest addition that keeps this site at or below the warning threshold through the horizon, "
                       "live at the earliest month its lead time allows.")
        if ss.get("_autosize_note"):
            st.caption(ss["_autosize_note"])

    with st.container(border=True):
        st.markdown("**Capital budget**")
        st.toggle("Cap new-project capital", key="budget_on", on_change=state.mark_custom,
                  help="Fund ranked projects in priority order while they fit (the Budget-Constrained preset uses $60M).")
        st.number_input("Capital budget ($M)", 0.0, 5_000.0, step=5.0, key="budget_m", on_change=state.mark_custom,
                        disabled=not ss["budget_on"])

    with st.expander("Capital & schedule assumptions"):
        st.caption("Illustrative order-of-magnitude unit costs and lead times (SOW Module 5). Presets keep these; "
                   "Reset to Baseline restores them.")
        u1, u2, u3 = st.columns(3)
        u1.number_input("$M per MW", 0.0, 50.0, step=0.1, format="%.2f", key="cost_mw_m")
        u2.number_input("$M per MWt", 0.0, 50.0, step=0.1, format="%.2f", key="cost_mwt_m")
        u3.number_input("$k per rack", 0.0, 500.0, step=1.0, format="%.0f", key="cost_rack_k")
        l1, l2, l3 = st.columns(3)
        l1.number_input("Power lead (mo)", 0, 60, key="lead_power")
        l2.number_input("Cooling lead (mo)", 0, 60, key="lead_cooling")
        l3.number_input("Rack lead (mo)", 0, 36, key="lead_space")

# ============================== Impact ==============================
with right:
    st.subheader(f"Impact vs Baseline · {sc.name}")
    kb, ks = base.kpis, r.kpis
    sb, s_ = base.site_summary.set_index("site_id"), r.site_summary.set_index("site_id")

    def _ec(k):
        e = k["earliest_constraint"]
        if not e:
            return "None", "no site reaches the warning line"
        tag = "runs out" if e["kind"] == "exhaustion" else f"reaches {warn:.0%}"
        return e["period"], f"{e['site_id']} {C.RESOURCE_LABELS[e['resource']].lower()} {tag}"

    v, sub = _ec(ks)
    vb, subb = _ec(kb)
    hi = lambda s: int(s["risk_status"].isin(["High", "Critical"]).sum())  # noqa: E731
    pd_b, pd_s = base.demand[:, 0, h].sum(), r.demand[:, 0, h].sum()
    cb = state.preset_plan("Baseline").proposed_total
    cs = ctx.plan.funded_total if sc.budget_usd is not None else ctx.plan.proposed_total
    ui.kpi_row([
        ui.kpi("Earliest site constraint", v, "", f"{sub} · baseline: {vb} ({subb})"),
        ui.kpi("Sites at High / Critical", f"{hi(s_)}", "of 6", f"baseline: {hi(sb)}"),
    ])
    st.write("")
    ui.kpi_row([
        ui.kpi(f"Portfolio power demand · M{h}", f"{pd_s:,.1f}", "MW", f"{pd_s - pd_b:+,.1f} MW vs baseline ({pd_b:,.1f})"),
        ui.kpi("New-project capex " + ("funded" if sc.budget_usd is not None else "needed"), f"${cs / 1e6:,.0f}", "M",
               f"{(cs - cb) / 1e6:+,.1f}M vs baseline (${cb / 1e6:,.0f}M) · sized to stay ≤ {warn:.0%} through M{h}"),
    ])
    st.write("")
    st.plotly_chart(charts.scenario_dumbbell(base, r), **charts.CHART_KW)
    st.caption("Circle = baseline, diamond = this scenario: each site's highest utilization at the horizon on any resource. "
               + ("With a budget set, funded projects are applied." if sc.budget_usd is not None else ""))

    risk_rows = []
    for sid in ids:
        eb, es = sb.loc[sid, "earliest_exhaustion_month"], s_.loc[sid, "earliest_exhaustion_month"]
        delta = "—"
        if pd.notna(eb) and pd.notna(es):
            dm = int(es) - int(eb)
            delta = "same" if dm == 0 else f"{abs(dm)} mo {'later' if dm > 0 else 'earlier'}"
        elif pd.notna(eb):
            delta = "avoided"
        elif pd.notna(es):
            delta = "new"
        risk_rows.append({
            "Site": f"{sid} · {names[sid]}",
            "Baseline": f"{T.RISK_ICONS[sb.loc[sid, 'risk_status']]} {sb.loc[sid, 'risk_status']}",
            "Scenario": f"{T.RISK_ICONS[s_.loc[sid, 'risk_status']]} {s_.loc[sid, 'risk_status']}",
            "First exhaustion (base)": ui.fmt_month(eb),
            "First exhaustion (scenario)": ui.fmt_month(es),
            "Change": delta,
            "First limit": C.RESOURCE_LABELS[s_.loc[sid, "limiting_resource"]],
        })
    st.dataframe(pd.DataFrame(risk_rows), hide_index=True)

# ============================== Presets side by side ==============================
st.subheader("Predefined scenarios side by side")
plans = {name: state.preset_plan(name) for name in state.PRESETS}
if ss["preset"] == "Custom":
    plans["Custom (current)"] = ctx.plan
comp, matrix = [], {sid: {"Site": f"{sid} · {names[sid]}"} for sid in ids}
for name, plan in plans.items():
    res_ = state.displayed(plan)
    k = res_.kpis
    e = k["earliest_constraint"]
    ssum = res_.site_summary.set_index("site_id")
    n_ex = int((res_.site_summary["earliest_exhaustion_month"].fillna(10**6) <= h).sum())
    comp.append({
        "Scenario": name,
        "Earliest site constraint": (f"{e['site_id']} {C.RESOURCE_LABELS[e['resource']].lower()} · {e['period']}"
                                     + ("" if e["kind"] == "exhaustion" else f" ({warn:.0%})")) if e else "None",
        f"Sites exhausting ≤ {h} mo": n_ex,
        "Sites High/Critical": int(ssum["risk_status"].isin(["High", "Critical"]).sum()),
        "Portfolio risk": f"{T.RISK_ICONS[k['portfolio_risk']]} {k['portfolio_risk']}",
        f"Power util. M{h}": res_.demand[:, 0, h].sum() / res_.capacity[:, 0, h].sum() * 100,
        f"Power demand M{h} (MW)": res_.demand[:, 0, h].sum(),
        "New capex proposed ($M)": plan.proposed_total / 1e6,
        "Funded ($M)": plan.funded_total / 1e6,
        "Over budget": int(len(plan.unfunded)) if not plan.projects.empty else 0,
    })
    for sid in ids:
        st_ = ssum.loc[sid, "risk_status"]
        exm = ssum.loc[sid, "earliest_exhaustion_month"]
        matrix[sid][name] = f"{T.RISK_ICONS[st_]} {st_} · " + (f"M{int(exm)}" if pd.notna(exm) else "—")
comp_df = pd.DataFrame(comp)
st.dataframe(comp_df, hide_index=True, column_config={
    f"Power util. M{h}": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%", color=T.ACCENT),
    f"Power demand M{h} (MW)": st.column_config.NumberColumn(format="%.1f"),
    "New capex proposed ($M)": st.column_config.NumberColumn(format="$%.1f"),
    "Funded ($M)": st.column_config.NumberColumn(format="$%.1f"),
})
st.dataframe(pd.DataFrame(list(matrix.values())), hide_index=True)
st.caption("Second table: each site's risk status and first exhaustion month under each scenario (— = none within 36 "
           "months). Presets use your current horizon, thresholds, unit costs and lead times. Budget-Constrained applies "
           "only the projects the $60M budget funds.")
slug = sc.name.lower().replace(" ", "_").replace("-", "_")
x1, x2, _ = st.columns([1, 1, 1])
x1.download_button("Scenario assumptions & results (CSV)", export.to_csv_bytes(export.scenario_results(r, ctx.plan)),
                   file_name=f"scenario_{slug}.csv", mime="text/csv", on_click="ignore", icon=":material/download:",
                   width="stretch", help="The active scenario: every assumption, portfolio KPIs, site results, capital totals.")
x2.download_button("Scenario comparison (CSV)", export.to_csv_bytes(comp_df), file_name="scenario_comparison.csv",
                   mime="text/csv", on_click="ignore", icon=":material/download:", width="stretch")

# ============================== Monte Carlo ==============================
st.subheader("Risk under uncertainty · Monte Carlo")
st.caption("The single-path forecast is the official answer. This layer re-runs the same engine across thousands of "
           "plausible futures (growth a few points higher or lower and correlated across sites, expansions slipping, "
           "efficiency only partly achieved) and counts how many run out, and when.")
with st.expander("Simulation settings"):
    m1, m2, m3 = st.columns(3)
    m1.select_slider("Simulations", options=[500, 1000, 2000, 5000, 10000], key="mc_n")
    m2.slider("Growth uncertainty (± pts, 1 sd)", 0.0, 15.0, step=0.5, key="mc_sd")
    m3.slider("Growth correlation between sites", 0.0, 1.0, step=0.05, key="mc_corr",
              help="Share of the growth shock common to every site (a regional AI boom hits everyone).")
    m4, m5, m6 = st.columns(3)
    m4.slider("Most likely slip (months)", 0, 12, key="mc_slip_mode")
    m5.slider("Worst-case slip (months)", 0, 24, key="mc_slip_max")
    m6.slider("Efficiency realised, worst case (%)", 0, 100, step=5, key="mc_eff_low")
    st.caption("Slip ~ Triangular(0, most likely, worst case) months added to every expansion; projects never land early.")

try:
    mc = state.monte_carlo(r.scenario)
    mc0 = state.monte_carlo(r.scenario, slip=False)
except ValidationError as e:
    st.error("**These simulation settings can't be run:**\n\n" + "\n".join(f"- {m}" for m in e.errors))
    st.stop()

site_mc = mc.site_summary().set_index("site_id")
ph = (mc.site_exhaust <= h).mean(axis=0)
hidden = [sid for sid in ids if pd.isna(r.site_summary.set_index("site_id").loc[sid, "earliest_exhaustion_month"])
          and site_mc.loc[sid, "prob_within_36m"] >= 0.10]
at25 = [f"{sid} {p:.0%}" for sid, p in zip(ids, ph) if p >= 0.25]
mk1, mk2, mk3 = st.columns(3)
mk1.markdown(ui.kpi(f"Sites with ≥ 25% chance of running out ≤ {h} mo", f"{len(at25)}", "of 6",
                    ", ".join(at25) or "none"), unsafe_allow_html=True)
mk2.markdown(ui.kpi("Hidden risk", f"{len(hidden)}", "sites",
                    "Single path never runs out, but ≥ 10% of futures do: "
                    + (", ".join(f"{sid} {site_mc.loc[sid, 'prob_within_36m']:.0%}" for sid in hidden) or "none")),
             unsafe_allow_html=True)
mk3.markdown(ui.kpi("Simulations", f"{mc.settings.n_draws:,}", "futures",
                    f"growth ±{mc.settings.growth_sd * 100:.1f} pts · slip ≤ {mc.settings.slip_max:g} mo · seed {mc.settings.seed}"),
             unsafe_allow_html=True)
st.write("")
g1, g2 = st.columns([3, 2], gap="large")
with g1:
    st.plotly_chart(charts.exhaustion_curves(mc, h), **charts.CHART_KW)
    st.caption("Share of simulated futures in which each site has run out of at least one resource by that month.")
with g2:
    st.markdown("**Where the risk view disagrees with the single path**")
    ins = mc_insights(mc, mc0)
    for i in ins:
        ui.insight_card(i)
    if not ins:
        st.caption("Nowhere: the simulated futures agree with the single-path forecast.")

tab = []
for sid in ids:
    s = site_mc.loc[sid]
    det = r.site_summary.set_index("site_id").loc[sid, "earliest_exhaustion_month"]
    q = lambda v: "—" if pd.isna(v) else f"M{int(v)}"  # noqa: E731
    tab.append({"Site": f"{sid} · {names[sid]}", "Single path": q(det), "P10": q(s.p10_month), "P50": q(s.p50_month),
                "P90": q(s.p90_month), "≤ 12 mo": s.prob_within_12m * 100, "≤ 24 mo": s.prob_within_24m * 100,
                "≤ 36 mo": s.prob_within_36m * 100})
pcol = lambda lbl: st.column_config.ProgressColumn(lbl, min_value=0, max_value=100, format="%.0f%%", color=T.STATUS["serious"])  # noqa: E731
mcol = lambda lbl, hlp: st.column_config.TextColumn(lbl, help=hlp)  # noqa: E731
mc_tbl = pd.DataFrame(tab)
st.dataframe(mc_tbl, hide_index=True, column_config={
    "Single path": mcol("Single-path month", "Deterministic first exhaustion (any resource)"),
    "P10": mcol("P10", "10% of futures run out by this month: a cautious planning date"),
    "P50": mcol("P50", "Median"), "P90": mcol("P90", "— = more than 10% of futures never run out within 36 months"),
    "≤ 12 mo": pcol("P(≤ 12 mo)"), "≤ 24 mo": pcol("P(≤ 24 mo)"), "≤ 36 mo": pcol("P(≤ 36 mo)"),
})
st.caption("Convention flip worth knowing: in solar finance P90 is the conservative *yield*; here P10 is the conservative "
           "*date*. Both mean plan on the cautious tail. Distributions are illustrative priors, not fitted to history.")
