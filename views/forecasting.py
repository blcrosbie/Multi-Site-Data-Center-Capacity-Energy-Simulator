"""Capacity Forecasting - monthly projections, exhaustion dates, first limiting resource,
energy/carbon/water run-rates and CSV exports (SOW Modules 3 & 7)."""

import math

import pandas as pd
import streamlit as st

from dcsim import config as C
from dcsim import export
from dcsim import theme as T
from dcsim.forecast import period_label
from dcsim.rules import fmt_amount
from dcsim.sustainability import carbon_contrast, footprint_now_vs_horizon
from dcsim.validation import ValidationError
from ui import charts, state
from ui import components as ui

ctx = state.context()
r, sc = ctx.result, ctx.scenario
h, warn, crit = sc.horizon, sc.warning_threshold, sc.critical_threshold
summary = r.site_summary.set_index("site_id")
sr = r.site_resource.set_index(["site_id", "resource"])
names = summary["site_name"]
label = C.RESOURCE_LABELS

ui.page_header("Capacity Forecasting", ctx)
st.caption("Monthly demand vs capacity for every site and resource, from the Oct 2026 synthetic baseline (actual, "
           f"month 0) through month {h} (projected). The Monte Carlo band shows the spread of "
           f"{int(st.session_state['mc_n']):,} simulated futures around the single-path forecast; the single path "
           "remains the official number.")

try:
    mc = state.monte_carlo(r.scenario)
except ValidationError as e:
    mc = None
    st.warning("Monte Carlo settings are invalid, so probability views are hidden: " + " ".join(e.errors))
mc_sr = mc.exhaustion_summary().set_index(["site_id", "resource"]) if mc else None
mc_ph = mc.prob_within(h) if mc else None

# --- Filters (one row, scoping everything below it) ----------------------------
ec = r.kpis["earliest_constraint"]
default_site = ec["site_id"] if ec else r.site_ids[0]
f1, f2, f3 = st.columns([2, 2, 1.2], vertical_alignment="bottom")
site = f1.selectbox("Facility", r.site_ids, index=r.site_ids.index(default_site), key="fc_site",
                    format_func=lambda s: f"{s} · {names[s]} ({summary.loc[s, 'risk_status']})")
res = f2.segmented_control("Resource", options=list(C.RESOURCES), key="fc_res",
                           default=summary.loc[default_site, "limiting_resource"],
                           format_func=label.get) or summary.loc[site, "limiting_resource"]
show_band = f3.toggle("Monte Carlo band", value=True, key="fc_band", disabled=mc is None,
                      help="Shade the 5–95% and 25–75% range of simulated demand (settings on Scenario Planning).")

i, j = r.site_ids.index(site), C.RESOURCES.index(res)
row, x = summary.loc[site], sr.loc[(site, res)]

# --- Site KPIs -----------------------------------------------------------------------
status = row["risk_status"]
if mc_sr is not None:
    m = mc_sr.loc[(site, res)]
    q = lambda v: f"M{int(v)}" if pd.notna(v) else ">36"  # noqa: E731
    mc_sub = f"Monte Carlo P10 {q(m.p10_month)} · P50 {q(m.p50_month)} · P90 {q(m.p90_month)}; {mc_ph[i, j]:.0%} within {h} mo"
else:
    mc_sub = "Monte Carlo unavailable"
ex = x.exhaustion_month
ui.kpi_row([
    ui.kpi("Site risk status", f"{T.RISK_ICONS[status]} {status}", "", f"Rule: {ui.esc(row['risk_reason'])}",
           accent=T.RISK_COLORS[status]),
    ui.kpi("First limiting resource", label[row["limiting_resource"]], "",
           f"Basis: {row['limiting_basis']}"
           + (f" · runs out {ui.fmt_month(row['earliest_exhaustion_month'])}" if pd.notna(row["earliest_exhaustion_month"]) else "")),
    ui.kpi(f"{label[res]} exhaustion", period_label(ex) if pd.notna(ex) else "None", "" if pd.notna(ex) else "within 36 mo",
           (f"Month {int(ex)} (single path). " if pd.notna(ex) else "Single path never exceeds capacity. ") + mc_sub),
    ui.kpi(f"{label[res]} utilization", f"{x.current_utilization:.0%} → {x.horizon_utilization:.0%}", "",
           f"Now → month {h}; headroom {fmt_amount(x.horizon_headroom, res)} at M{h}",
           badge=ui.chip(ui.util_status(x.horizon_utilization, warn, crit))),
])

# --- Site charts -----------------------------------------------------------------------
st.write("")
c1, c2 = st.columns([3, 2], gap="large")
with c1:
    st.subheader(f"{site} {names[site]} · {label[res].lower()} demand vs capacity")
    bands = mc.demand_bands(res, site) if (mc is not None and show_band) else None
    st.plotly_chart(charts.site_forecast(r, site, res, bands), **charts.CHART_KW)
    st.caption("Square = actual synthetic baseline. Dashed = projected demand (single path). Steps = planned or "
               "scenario expansions going live. Shading = Monte Carlo spread; dotted gray = capacity when projects slip.")
with c2:
    st.subheader("What fills up first")
    st.plotly_chart(charts.resource_utilization(r, site), **charts.CHART_KW)
    st.caption(f"All three resources at {site} on one % scale. Highlighted = first limiting resource "
               f"({row['limiting_basis']}). Fixing anything else does not help until this one is fixed.")

# --- Show the math -----------------------------------------------------------------------
d0 = float(r.demand[i, j, 0])
g = float(r.growth[i])
e = sc.efficiency if res != "space" else 0.0
g_eff = (1 + g) / (1 + sc.density_growth) - 1 if res == "space" else g
cap_used = float(r.capacity[i, j, int(ex)]) if pd.notna(ex) else float(r.capacity[i, j, C.MAX_HORIZON])
unit = C.RESOURCE_UNITS[res]
with st.expander(f"Show the math: when does {site} run out of {label[res].lower()}?"):
    lines = [f"D0 = {d0:,.2f} {unit} (Oct 2026 actual)    C = {cap_used:,.2f} {unit}    "
             f"g = {g_eff:.1%}/yr" + ("  (site growth net of 3%/yr rack densification)" if res == "space" else "")
             + (f"    e = {e:.0%}" if e else "")]
    if g_eff > 0 and d0 > 0:
        base = d0 * (1 - e)
        lines.append(f"{base:,.2f} × (1 + {g_eff:.3f})^(m/12) > {cap_used:,.2f}")
        ratio = cap_used / base
        mstar = 12 * math.log(ratio) / math.log(1 + g_eff)
        lines.append(f"m > 12 × ln({cap_used:,.2f} / {base:,.2f}) / ln({1 + g_eff:.3f}) "
                     f"= 12 × {math.log(ratio):.4f} / {math.log(1 + g_eff):.4f} ≈ {mstar:.1f}")
        first = max(1, math.floor(mstar) + 1)
        engine = f"engine: month {int(ex)} ✓" if pd.notna(ex) and int(ex) == first else (
            f"engine: month {int(ex)}" if pd.notna(ex) else "engine: not within 36 months ✓" if first > C.MAX_HORIZON
            else "engine: see capacity steps")
        lines.append(f"first whole month: m = {first}  →  {period_label(first) if first <= 120 else 'beyond 2036'}"
                     f"        {engine}")
    else:
        lines.append("Demand is not growing, so it never crosses capacity.")
    if res in ("power", "cooling"):
        pue = float(ctx.sites["pue"].iat[i])
        it = float(r.demand[i, C.RESOURCES.index("power"), 0]) / pue
        heat = it * C.COOLING_HEAT_FACTOR
        lines += ["", f"IT load = facility power / PUE = {r.demand[i, 0, 0]:,.2f} / {pue:.2f} ≈ {it:,.2f} MW",
                  f"heat ≈ IT × {C.COOLING_HEAT_FACTOR} ≈ {heat:,.2f} MWt ≈ {heat * C.MWT_TO_TONS:,.0f} tons of refrigeration"]
    ui.mathbox("\n".join(lines))
    st.caption("Closed form of the SOW formula demand × (1 + g)^(m/12) > capacity, using the capacity in effect in the "
               "month it runs out (planned expansions included). Exhaustion is the first month demand strictly exceeds "
               "capacity, hence floor(m) + 1. The same hand-worked cases run in the automated test suite.")

# --- Portfolio status timeline -----------------------------------------------------------
st.subheader("Capacity status timeline · all sites")
st.plotly_chart(charts.status_heatmap(r), **charts.CHART_KW)
st.caption(f"One row per site and resource, one cell per month (first column = Oct 2026 actual). Colour = utilization "
           f"band: below {warn:.0%}, warning ≥ {warn:.0%}, critical ≥ {crit:.0%}, over capacity > 100%. "
           "Blue ticks = expansions going live. Hover any cell for the exact utilization.")

# --- Exhaustion & threshold dates ---------------------------------------------------------
st.subheader("Exhaustion dates and first limiting resource")
rows = []
for sid in r.site_ids:
    for k, res_ in enumerate(C.RESOURCES):
        y = sr.loc[(sid, res_)]
        lim = summary.loc[sid, "limiting_resource"] == res_
        m = mc_sr.loc[(sid, res_)] if mc_sr is not None else None
        mm = lambda v: "—" if (m is None or pd.isna(v)) else f"M{int(v)}"  # noqa: E731
        rows.append({
            "Site": f"{sid} · {names[sid]}",
            "Resource": label[res_] + ("  ◂ first limit" if lim else ""),
            "Now": y.current_utilization * 100,
            f"M{h}": y.horizon_utilization * 100,
            f"{warn:.0%} reached": ui.fmt_month(y.warning_month),
            f"{crit:.0%} reached": ui.fmt_month(y.critical_month),
            "Exhaustion": ui.fmt_month(y.exhaustion_month),
            "MC P10": mm(m.p10_month) if m is not None else "—",
            "MC P50": mm(m.p50_month) if m is not None else "—",
            "MC P90": mm(m.p90_month) if m is not None else "—",
            f"P(≤{h} mo)": float(mc_ph[r.site_ids.index(sid), k]) * 100 if mc_ph is not None else None,
        })
util_col = lambda lbl: st.column_config.ProgressColumn(lbl, min_value=0, max_value=100, format="%.0f%%", color=T.ACCENT)  # noqa: E731
ex_tbl = pd.DataFrame(rows)
st.dataframe(
    ex_tbl, hide_index=True, row_height=30, height=30 * (len(rows) + 1) + 3,
    column_config={
        "Now": util_col("Util. now"), f"M{h}": util_col(f"Util. M{h}"),
        "MC P10": st.column_config.TextColumn("MC P10", help="10% of simulated futures run out by this month"),
        "MC P50": st.column_config.TextColumn("MC P50", help="Median simulated exhaustion month"),
        "MC P90": st.column_config.TextColumn("MC P90", help="— = more than 10% of futures never run out within 36 months"),
        f"P(≤{h} mo)": st.column_config.ProgressColumn(f"P(exhaust ≤ {h} mo)", min_value=0, max_value=100,
                                                         format="%.0f%%", color=T.STATUS["serious"]),
    },
)
st.caption("Dates come from the single-path forecast (36-month lookahead, so risk does not change with the display "
           "horizon). MC columns: — means not within 36 months in that share of futures. A P10 date well before the "
           "single-path date means the date is fragile. Plan on the cautious tail.")

# --- Energy, carbon and water ----------------------------------------------------------------
st.subheader("Energy, carbon and water")
fp = footprint_now_vs_horizon(r)
tot = fp[["annual_energy_mwh", "annual_energy_mwh_h", "annual_emissions_tco2e", "annual_emissions_tco2e_h",
          "annual_water_ml", "annual_water_ml_h"]].sum()
ui.kpi_row([
    ui.kpi("Annual energy (run-rate)", f"{tot.annual_energy_mwh / 1000:,.0f}", "GWh/yr",
           f"→ {tot.annual_energy_mwh_h / 1000:,.0f} GWh/yr at M{h} (projected)"),
    ui.kpi("Scope 2 emissions", f"{tot.annual_emissions_tco2e / 1000:,.0f}", "kt CO₂e/yr",
           f"→ {tot.annual_emissions_tco2e_h / 1000:,.0f} kt/yr at M{h}; location-based, average grid rates"),
    ui.kpi("Site water use", f"{tot.annual_water_ml:,.0f}", "ML/yr",
           f"→ {tot.annual_water_ml_h:,.0f} million litres/yr at M{h}; IT energy × WUE"),
    ui.kpi("Portfolio PUE (energy-weighted)",
           f"{tot.annual_energy_mwh / (fp['annual_energy_mwh'] / fp['pue']).sum():.2f}", "",
           f"Best {fp['pue'].min():.2f} ({fp.loc[fp['pue'].idxmin(), 'site_id']}) · "
           f"worst {fp['pue'].max():.2f} ({fp.loc[fp['pue'].idxmax(), 'site_id']})"),
])
st.write("")
e1, e2 = st.columns(2, gap="large")
with e1:
    st.plotly_chart(charts.footprint_bars(fp, h), **charts.CHART_KW)
with e2:
    tbl = pd.DataFrame({
        "Site": fp["site_id"] + " · " + fp["site_name"],
        "Energy (GWh/yr)": fp["annual_energy_mwh"] / 1000,
        "PUE": fp["pue"],
        "Grid (t/MWh)": fp["grid_carbon_t_per_mwh"],
        "Emissions (kt/yr)": fp["annual_emissions_tco2e"] / 1000,
        "WUE (L/kWh)": fp["wue_l_per_kwh"],
        "Water (ML/yr)": fp["annual_water_ml"],
    })
    st.dataframe(tbl, hide_index=True, column_config={
        "Energy (GWh/yr)": st.column_config.NumberColumn(format="%.0f"),
        "Emissions (kt/yr)": st.column_config.ProgressColumn(min_value=0, max_value=float(tbl["Emissions (kt/yr)"].max()) * 1.05,
                                                             format="%.1f", color=T.ACCENT),
        "Water (ML/yr)": st.column_config.ProgressColumn(min_value=0, max_value=float(tbl["Water (ML/yr)"].max()) * 1.05,
                                                         format="%.0f", color=T.ACCENT),
        "Grid (t/MWh)": st.column_config.NumberColumn(format="%.2f"),
    })
    cc = carbon_contrast(r)
    if cc:
        ui.section_note(
            f"Where load lands matters more than how efficiently it runs: {cc['dirty']['site_id']} draws "
            f"{cc['power_ratio'] - 1:+.0%} the power of {cc['clean']['site_id']} but emits {cc['emissions_ratio']:.1f}× the CO₂. "
            f"Run-rate = the month's facility draw held for 8,760 h. Average location-based grid intensity; marginal "
            f"rates may differ. These metrics inform siting but do not constrain capacity in this model.")

# --- Monthly data + exports ---------------------------------------------------------------------
st.subheader("Monthly forecast data")
t1, t2 = st.columns(2)
sel_sites = t1.multiselect("Sites", r.site_ids, default=[site], key="fc_tbl_sites",
                           format_func=lambda s: f"{s} · {names[s]}")
sel_res = t2.multiselect("Resources", list(C.RESOURCES), default=list(C.RESOURCES), key="fc_tbl_res",
                         format_func=label.get)
mon = r.monthly[r.monthly["site_id"].isin(sel_sites) & r.monthly["resource"].isin(sel_res)]
st.dataframe(
    mon[["site_id", "resource", "unit", "month", "period", "data_type", "demand", "capacity", "headroom", "utilization"]],
    hide_index=True, height=320,
    column_config={
        "demand": st.column_config.NumberColumn(format="%.2f"),
        "capacity": st.column_config.NumberColumn(format="%.2f"),
        "headroom": st.column_config.NumberColumn(format="%.2f"),
        "utilization": st.column_config.NumberColumn(format="percent"),
        "data_type": st.column_config.TextColumn("Data type", help="Month 0 = actual synthetic baseline; later months projected"),
    },
)
slug = sc.name.lower().replace(" ", "_").replace("-", "_")
d1, d2, _ = st.columns([1, 1, 1])
d1.download_button("Monthly capacity forecast (CSV)", export.to_csv_bytes(export.monthly_forecast(r)),
                   file_name=f"monthly_forecast_{slug}.csv", mime="text/csv", on_click="ignore",
                   icon=":material/download:", width="stretch",
                   help=f"All sites and resources, months 0–{h}; month 0 labelled actual.")
d2.download_button("Facility-level capacity data (CSV)", export.to_csv_bytes(export.facility_capacity(r)),
                   file_name=f"facility_capacity_{slug}.csv", mime="text/csv", on_click="ignore",
                   icon=":material/download:", width="stretch",
                   help="One row per site and resource: today, horizon, thresholds, exhaustion, risk status.")

# --- Assumptions ------------------------------------------------------------------------------
with st.expander("Forecasting assumptions"):
    growth = ", ".join(f"{sid} {gg:.1%}" for sid, gg in zip(r.site_ids, r.growth))
    st.markdown(
        f"""
- **Demand (power, cooling):** `baseline × (1 + g)^(m/12) × (1 − e)` for months ≥ 1; month 0 is the measured baseline.
  Growth applied: {growth}. Efficiency `e` = {sc.efficiency:.0%}.
- **Racks:** `baseline × ((1 + g) / (1 + d))^(m/12)`, with rack densification `d` = {sc.density_growth:.0%}/yr
  (more kW per rack means fewer new racks per MW).
- **Cooling** is heat: IT load × {C.COOLING_HEAT_FACTOR} MWt per MW; 1 MWt = {C.MWT_TO_TONS:.1f} tons of refrigeration.
- **Capacity** = installed usable capacity + every expansion from its activation month
  ({'planned expansions included' if sc.include_planned else 'planned expansions excluded'}).
- **Exhaustion** = first month demand strictly exceeds capacity. Thresholds: warning {warn:.0%}, critical {crit:.0%}.
- **First limiting resource** = earliest exhaustion; else earliest warning breach; else highest utilization at M{h}.
- **Monte Carlo** re-runs the same engine with uncertain growth, schedule slip and efficiency realisation
  (settings on Scenario Planning).
- **Energy run-rate** = facility MW × 8,760 h; emissions = energy × grid intensity; water = IT energy × WUE.

Full formulas: [Methodology]({ui.METHODOLOGY_URL}).
"""
    )
