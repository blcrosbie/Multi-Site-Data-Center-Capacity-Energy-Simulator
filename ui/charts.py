"""Plotly figures: boxed plot area, monospace ticks, single y-axis, thin marks,
fixed site colours plus dash patterns, hover on every chart.

Rendered with Streamlit's Plotly theme (theme="streamlit"), which supplies the
background and text colours of whichever Light / Dark theme is active. Neutrals
here are semi-transparent grays so they read on navy and on white alike."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from dcsim import config as C
from dcsim import theme as T
from dcsim.forecast import ForecastResult, period_label

FONT = ", ".join(T.MONO_FAMILY)
GRIDC = "rgba(138,138,138,0.28)"   # gridlines on either background
LINEC = "rgba(138,138,138,0.75)"   # plot frame and ticks
CHART_KW = dict(theme="streamlit", config={"displaylogo": False})


def _layout(fig: go.Figure, title: str | None = None, height: int = 380, ytitle: str = "",
            yformat: str | None = None) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=15), x=0, xanchor="left") if title else None,
        height=height,
        margin=dict(l=8, r=8, t=48 if title else 16, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=13),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, font=dict(size=11),
                    bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(font=dict(family=FONT)),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor=GRIDC, linecolor=LINEC, showline=True, mirror=True, ticks="outside",
                     tickcolor=LINEC, zeroline=False,
                     showspikes=True, spikecolor=LINEC, spikethickness=1, spikedash="solid",
                     automargin=True)
    fig.update_yaxes(gridcolor=GRIDC, linecolor=LINEC, showline=True, mirror=True, ticks="outside",
                     tickcolor=LINEC, zeroline=False,
                     title=dict(text=ytitle, font=dict(size=12), standoff=8), tickformat=yformat,
                     rangemode="tozero", automargin=True)
    return fig


def _legend_below(fig: go.Figure, entrywidth: int = 150) -> None:
    # Fixed entry widths: Plotly measures text before the web font loads, which overlaps entries.
    fig.update_layout(legend=dict(orientation="h", y=-0.12, yanchor="top", x=0, xanchor="left",
                                  entrywidthmode="pixels", entrywidth=entrywidth))


def _threshold_key(fig: go.Figure, name: str, color: str) -> None:
    """Legend entry for a threshold line (reference lines carry no legend of their own)."""
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", name=name, hoverinfo="skip",
                             line=dict(color=color, width=1.2, dash="dot")))


def _dates(periods) -> pd.Series:
    return pd.to_datetime(pd.Series(periods), format="%Y-%m")


def portfolio_forecast(result: ForecastResult, resource: str = "power") -> go.Figure:
    """Portfolio demand vs capacity, with the earliest site-level exhaustion marked."""
    sc = result.scenario
    pm = result.portfolio_monthly[result.portfolio_monthly["resource"] == resource]
    x = _dates(pm["period"])
    unit = C.RESOURCE_UNITS[resource]
    fmt = ",.0f" if resource == "space" else ",.1f"
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=pm["capacity"], name="Capacity", mode="lines", line_shape="hv",
                             line=dict(color=T.NEUTRAL, width=2),
                             hovertemplate=f"%{{y:{fmt}}} {unit}<extra>Capacity</extra>"))
    fig.add_trace(go.Scatter(x=x, y=pm["capacity"] * sc.warning_threshold, mode="lines", line_shape="hv",
                             name=f"{sc.warning_threshold:.0%} warning",
                             line=dict(color=T.STATUS["warning"], width=1.2, dash="dot"),
                             hovertemplate=f"%{{y:{fmt}}} {unit}<extra>Warning threshold</extra>"))
    fig.add_trace(go.Scatter(x=x, y=pm["demand"], name="Projected demand", mode="lines",
                             line=dict(color=T.ACCENT, width=2, dash="dash"),
                             hovertemplate=f"%{{y:{fmt}}} {unit}<extra>Demand</extra>"))
    fig.add_trace(go.Scatter(x=x[:1], y=pm["demand"].iloc[:1], name="Actual (Oct 2026)", mode="markers",
                             marker=dict(size=10, symbol="square", color=T.ACCENT),
                             hovertemplate=f"%{{y:{fmt}}} {unit}<extra>Actual</extra>"))

    ec = result.kpis["earliest_constraint"]
    first = result.site_resource[result.site_resource["resource"] == resource].dropna(subset=["exhaustion_month"])
    if len(first):
        r = first.sort_values("exhaustion_month").iloc[0]
        m = int(r["exhaustion_month"])
        if m <= sc.horizon:
            xd = pd.to_datetime(period_label(m, "%Y-%m"), format="%Y-%m")
            fig.add_vline(x=xd, line=dict(color=T.STATUS["critical"], width=1.5, dash="dot"))
            fig.add_annotation(x=xd, y=1, yref="paper", yanchor="bottom", showarrow=False, xanchor="left",
                               text=f"■ {r['site_id']} exhausts {C.RESOURCE_LABELS[resource].lower()} ({period_label(m)})",
                               font=dict(size=11), bordercolor=T.STATUS["critical"], borderwidth=1, xshift=4)
    _layout(fig, None, 400, f"{C.RESOURCE_LABELS[resource]} ({unit})")
    _legend_below(fig, 135)
    fig.update_layout(margin=dict(t=28))
    fig.update_xaxes(hoverformat="%b %Y")
    return fig


def site_utilization(result: ForecastResult, resource: str = "power") -> go.Figure:
    """Utilization by site: now (bar) vs end of horizon (marker), with threshold lines."""
    sc, h = result.scenario, result.horizon
    sr = result.site_resource[result.site_resource["resource"] == resource].iloc[::-1]  # DC1 on top
    labels = sr["site_id"] + " " + sr["site_name"]
    fig = go.Figure()
    fig.add_trace(go.Bar(y=labels, x=sr["current_utilization"], orientation="h", name="Now (actual)",
                         marker=dict(color=T.ACCENT, line=dict(width=0)), width=0.5,
                         hovertemplate="%{x:.0%}<extra>Now</extra>"))
    fig.add_trace(go.Scatter(y=labels, x=sr["horizon_utilization"], mode="markers", name=f"M{h} (proj.)",
                             marker=dict(symbol="diamond", size=12, color=T.NEUTRAL),
                             hovertemplate="%{x:.0%}<extra>Month " + str(h) + "</extra>"))
    for thr, color, name in ((sc.warning_threshold, T.STATUS["warning"], "Warning"),
                             (sc.critical_threshold, T.STATUS["serious"], "Critical"),
                             (1.0, T.STATUS["critical"], "Capacity")):
        fig.add_vline(x=thr, line=dict(color=color, width=1.2, dash="dot"))
        _threshold_key(fig, f"{name} {thr:.0%}", color)
    xmax = max(1.1, float(sr["horizon_utilization"].max()) * 1.08)
    _layout(fig, None, 400)
    _legend_below(fig, 150)
    fig.update_layout(hovermode="y unified", margin=dict(t=16), bargap=0.4)
    fig.update_xaxes(tickformat=".0%", range=[0, xmax], showspikes=False)
    fig.update_yaxes(showgrid=False, rangemode=None)
    return fig


def site_utilization_forecast(result: ForecastResult, site_ids: list[str], resource: str = "power") -> go.Figure:
    """Utilization over time for the selected sites (common % scale), with thresholds."""
    sc = result.scenario
    m = result.monthly[(result.monthly["resource"] == resource) & (result.monthly["site_id"].isin(site_ids))]
    fig = go.Figure()
    for sid in site_ids:
        g = m[m["site_id"] == sid]
        fig.add_trace(go.Scatter(
            x=_dates(g["period"]), y=g["utilization"], mode="lines", name=f"{sid} {g['site_name'].iloc[0]}",
            line=dict(color=T.SITE_COLORS[sid], width=2, dash=T.SITE_DASH[sid]),
            hovertemplate="%{y:.0%}<extra>" + sid + "</extra>",
        ))
    for thr, color, name in ((sc.warning_threshold, T.STATUS["warning"], "Warning"),
                             (sc.critical_threshold, T.STATUS["serious"], "Critical"),
                             (1.0, T.STATUS["critical"], "Capacity (100%)")):
        fig.add_hline(y=thr, line=dict(color=color, width=1.2, dash="dot"),
                      annotation=dict(text=f"{name} {thr:.0%}" if thr < 1 else name, font=dict(size=10),
                                      xanchor="left", x=0, yanchor="bottom"))
    _layout(fig, None, 400, f"{C.RESOURCE_LABELS[resource]} utilization", ".0%")
    _legend_below(fig, 165)
    fig.update_layout(margin=dict(t=16))
    fig.update_xaxes(hoverformat="%b %Y")
    return fig


# --- Shared helpers for the forecasting / scenario / decision pages ---------------

RES_DASH = {"power": "solid", "cooling": "dash", "space": "dot"}
RES_SHORT = {"power": "power", "cooling": "cooling", "space": "racks"}
BANDS = (  # utilization status bands: (label, colour, icon) - status colours mean status only
    ("Below warning", "rgba(138,138,138,0.16)", T.RISK_ICONS["Low"]),
    ("Warning", T.STATUS["warning"], T.RISK_ICONS["Medium"]),
    ("Critical", T.STATUS["serious"], T.RISK_ICONS["High"]),
    ("Over capacity", T.STATUS["critical"], T.RISK_ICONS["Critical"]),
)


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _month_dates(n: int) -> pd.Series:
    return _dates([period_label(m, "%Y-%m") for m in range(n)])


def _mdate(m: int):
    return pd.to_datetime(period_label(int(m), "%Y-%m"), format="%Y-%m")


def _fmt(resource: str) -> str:
    return ",.0f" if resource == "space" else ",.1f"


def status_heatmap(result: ForecastResult) -> go.Figure:
    """Every site x resource row, every month: which utilization band it sits in."""
    sc, h = result.scenario, result.horizon
    util = result.demand / result.capacity
    x = _month_dates(h + 1)
    rows, z, text, cd = [], [], [], []
    names = [label for label, _, _ in BANDS]
    for i, sid in enumerate(result.site_ids):
        for j, res in enumerate(C.RESOURCES):
            u = util[i, j, : h + 1]
            code = np.select([u > 1, u >= sc.critical_threshold, u >= sc.warning_threshold], [3, 2, 1], 0)
            rows.append(f"{sid} {RES_SHORT[res]}")
            z.append(code)
            text.append([names[c] for c in code])
            cd.append(u)
    n = len(BANDS)
    cs = []
    for k, (_, color, _) in enumerate(BANDS):
        cs += [[k / n, color], [(k + 1) / n, color]]
    fig = go.Figure(go.Heatmap(
        z=z, x=x, y=rows, zmin=-0.5, zmax=n - 0.5, colorscale=cs, showscale=False, xgap=1, ygap=1,
        customdata=cd, text=text,
        hovertemplate="%{y} · %{x|%b %Y}<br>%{customdata:.0%} utilized · %{text}<extra></extra>",
    ))
    # Expansions going live: a tick on the row where capacity steps up.
    ex = [e for e in result.expansions if e.activation_month <= h]
    if ex:
        fig.add_trace(go.Scatter(
            x=[_mdate(e.activation_month) for e in ex], y=[f"{e.site_id} {RES_SHORT[e.resource]}" for e in ex],
            mode="markers", name="Expansion goes live",
            marker=dict(symbol="line-ns-open", size=16, color=T.ACCENT, line=dict(width=3, color=T.ACCENT)),
            customdata=[f"+{e.amount:g} {C.RESOURCE_UNITS[e.resource]} · {e.label}" for e in ex],
            hovertemplate="%{customdata}<extra>%{x|%b %Y}</extra>",
        ))
    for label, color, icon in BANDS:
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=f"{icon} {label}", hoverinfo="skip",
                                 marker=dict(symbol="square", size=12, color=color, line=dict(width=1, color=LINEC))))
    for k in range(1, len(result.site_ids)):  # rule between sites
        fig.add_hline(y=k * len(C.RESOURCES) - 0.5, line=dict(color=LINEC, width=1))
    _layout(fig, None, 22 * len(rows) + 90)
    _legend_below(fig, 150)
    fig.update_layout(hovermode="closest", margin=dict(t=12))
    fig.update_yaxes(autorange="reversed", showgrid=False, rangemode=None, ticks="")
    fig.update_xaxes(showgrid=False, showspikes=False)
    return fig


def site_forecast(result: ForecastResult, site_id: str, resource: str, bands: pd.DataFrame | None = None) -> go.Figure:
    """One site and resource: capacity steps, thresholds, projected demand, optional Monte Carlo fan."""
    sc, h = result.scenario, result.horizon
    i, j = result.site_ids.index(site_id), C.RESOURCES.index(resource)
    x = _month_dates(h + 1)
    d, c = result.demand[i, j, : h + 1], result.capacity[i, j, : h + 1]
    unit, fmt = C.RESOURCE_UNITS[resource], _fmt(resource)
    fig = go.Figure()
    if bands is not None:
        b = bands.iloc[: h + 1]
        for lo, hi, a, name in (("demand_p05", "demand_p95", 0.13, "Monte Carlo 5–95%"),
                                ("demand_p25", "demand_p75", 0.26, "Monte Carlo 25–75%")):
            fig.add_trace(go.Scatter(x=x, y=b[hi], mode="lines", line=dict(width=0), hoverinfo="skip",
                                     showlegend=False))
            fig.add_trace(go.Scatter(x=x, y=b[lo], mode="lines", line=dict(width=0), fill="tonexty",
                                     fillcolor=_rgba(T.ACCENT, a), name=name, customdata=b[hi],
                                     hovertemplate=f"%{{y:{fmt}}}–%{{customdata:{fmt}}} {unit}<extra>{name}</extra>"))
        if not np.allclose(b["capacity_p10"].to_numpy(), c):
            fig.add_trace(go.Scatter(x=x, y=b["capacity_p10"], mode="lines", line_shape="hv",
                                     name="Capacity if projects slip (P10)",
                                     line=dict(color=T.NEUTRAL, width=1.2, dash="dot"),
                                     hovertemplate=f"%{{y:{fmt}}} {unit}<extra>Capacity P10</extra>"))
    fig.add_trace(go.Scatter(x=x, y=c, name="Capacity", mode="lines", line_shape="hv",
                             line=dict(color=T.NEUTRAL, width=2),
                             hovertemplate=f"%{{y:{fmt}}} {unit}<extra>Capacity</extra>"))
    fig.add_trace(go.Scatter(x=x, y=c * sc.warning_threshold, mode="lines", line_shape="hv",
                             name=f"{sc.warning_threshold:.0%} warning",
                             line=dict(color=T.STATUS["warning"], width=1.2, dash="dot"),
                             hovertemplate=f"%{{y:{fmt}}} {unit}<extra>Warning</extra>"))
    fig.add_trace(go.Scatter(x=x, y=c * sc.critical_threshold, mode="lines", line_shape="hv",
                             name=f"{sc.critical_threshold:.0%} critical",
                             line=dict(color=T.STATUS["serious"], width=1.2, dash="dot"),
                             hovertemplate=f"%{{y:{fmt}}} {unit}<extra>Critical</extra>"))
    fig.add_trace(go.Scatter(x=x, y=d, name="Projected demand", mode="lines",
                             line=dict(color=T.ACCENT, width=2, dash="dash"),
                             hovertemplate=f"%{{y:{fmt}}} {unit}<extra>Demand</extra>"))
    fig.add_trace(go.Scatter(x=x[:1], y=d[:1], name="Actual (Oct 2026)", mode="markers",
                             marker=dict(size=10, symbol="square", color=T.ACCENT),
                             hovertemplate=f"%{{y:{fmt}}} {unit}<extra>Actual</extra>"))
    for e in result.expansions:
        if e.site_id == site_id and e.resource == resource and e.activation_month <= h:
            fig.add_annotation(x=_mdate(e.activation_month), y=float(c[e.activation_month]), showarrow=True,
                               arrowhead=0, ax=0, ay=-26, text=f"+{e.amount:g} {unit}", font=dict(size=11),
                               arrowcolor=LINEC)
    sr = result.site_resource.set_index(["site_id", "resource"]).loc[(site_id, resource)]
    if pd.notna(sr.exhaustion_month) and sr.exhaustion_month <= h:
        xd = _mdate(sr.exhaustion_month)
        fig.add_vline(x=xd, line=dict(color=T.STATUS["critical"], width=1.5, dash="dot"))
        fig.add_annotation(x=xd, y=1, yref="paper", yanchor="bottom", showarrow=False, xanchor="left", xshift=4,
                           text=f"■ runs out {period_label(sr.exhaustion_month)}", font=dict(size=11),
                           bordercolor=T.STATUS["critical"], borderwidth=1)
    _layout(fig, None, 420, f"{C.RESOURCE_LABELS[resource]} ({unit})")
    _legend_below(fig, 175)
    fig.update_layout(margin=dict(t=28))
    fig.update_xaxes(hoverformat="%b %Y")
    return fig


def resource_utilization(result: ForecastResult, site_id: str) -> go.Figure:
    """What fills up first: all three resources at one site, the first limiting one emphasised."""
    sc, h = result.scenario, result.horizon
    i = result.site_ids.index(site_id)
    lim = result.site_summary.set_index("site_id").loc[site_id, "limiting_resource"]
    x = _month_dates(h + 1)
    fig = go.Figure()
    for j, res in enumerate(C.RESOURCES):
        u = result.demand[i, j, : h + 1] / result.capacity[i, j, : h + 1]
        is_lim = res == lim
        fig.add_trace(go.Scatter(
            x=x, y=u, mode="lines", name=C.RESOURCE_LABELS[res] + (" ◂ first limit" if is_lim else ""),
            line=dict(color=T.SITE_COLORS[site_id] if is_lim else T.NEUTRAL, width=2.6 if is_lim else 1.6,
                      dash=RES_DASH[res]),
            hovertemplate="%{y:.0%}<extra>" + RES_SHORT[res] + "</extra>",
        ))
        if is_lim:
            fig.add_annotation(x=x.iloc[-1], y=float(u[-1]), text=f"{RES_SHORT[res]} {u[-1]:.0%}", showarrow=False,
                               xanchor="left", xshift=6, font=dict(size=11))
    for thr, color, name in ((sc.warning_threshold, T.STATUS["warning"], "Warning"),
                             (sc.critical_threshold, T.STATUS["serious"], "Critical"),
                             (1.0, T.STATUS["critical"], "Capacity")):
        fig.add_hline(y=thr, line=dict(color=color, width=1.2, dash="dot"))
        _threshold_key(fig, f"{name} {thr:.0%}", color)
    _layout(fig, None, 420, "Utilization", ".0%")
    _legend_below(fig, 215)
    fig.update_layout(margin=dict(t=16, r=60))
    fig.update_xaxes(hoverformat="%b %Y")
    return fig


def exhaustion_curves(mc, horizon: int) -> go.Figure:
    """Monte Carlo: cumulative probability each site has run out of any resource by month m."""
    ec = mc.exhaustion_curve()
    fig = go.Figure()
    for sid, name in zip(mc.site_ids, mc.site_names):
        g = ec[ec["site_id"] == sid]
        fig.add_trace(go.Scatter(
            x=_dates([period_label(m, "%Y-%m") for m in g["month"]]), y=g["probability"], mode="lines",
            name=f"{sid} {name}", line=dict(color=T.SITE_COLORS[sid], width=2, dash=T.SITE_DASH[sid]),
            hovertemplate="%{y:.0%}<extra>" + sid + "</extra>",
        ))
    if horizon < C.MAX_HORIZON:
        fig.add_vline(x=_mdate(horizon), line=dict(color=LINEC, width=1.2, dash="dot"))
        fig.add_annotation(x=_mdate(horizon), y=1, yref="paper", yanchor="bottom", showarrow=False, xanchor="left",
                           xshift=4, text=f"horizon · {horizon} mo", font=dict(size=11))
    _layout(fig, None, 400, "P(any resource exhausted)", ".0%")
    _legend_below(fig, 165)
    fig.update_layout(margin=dict(t=24))
    fig.update_yaxes(range=[0, 1.04], rangemode=None)
    fig.update_xaxes(hoverformat="%b %Y")
    return fig


def scenario_dumbbell(base: ForecastResult, scen: ForecastResult) -> go.Figure:
    """Highest utilization at the horizon (any resource), per site: baseline -> scenario."""
    sc, h = scen.scenario, scen.horizon
    ub = base.demand[:, :, h] / base.capacity[:, :, h]
    us = scen.demand[:, :, h] / scen.capacity[:, :, h]
    labels = [f"{sid} {n}" for sid, n in zip(scen.site_ids, scen.sites["site_name"])][::-1]
    b, s = ub.max(axis=1)[::-1], us.max(axis=1)[::-1]
    rb = [RES_SHORT[C.RESOURCES[k]] for k in ub.argmax(axis=1)][::-1]
    rs = [RES_SHORT[C.RESOURCES[k]] for k in us.argmax(axis=1)][::-1]
    fig = go.Figure()
    xs, ys = [], []
    for lab, x0, x1 in zip(labels, b, s):
        xs += [x0, x1, None]
        ys += [lab, lab, None]
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color=LINEC, width=2), hoverinfo="skip",
                             showlegend=False))
    fig.add_trace(go.Scatter(x=b, y=labels, mode="markers", name="Baseline", customdata=rb,
                             marker=dict(symbol="circle", size=11, color=T.NEUTRAL),
                             hovertemplate="%{x:.0%} (%{customdata})<extra>Baseline</extra>"))
    fig.add_trace(go.Scatter(x=s, y=labels, mode="markers", name="This scenario", customdata=rs,
                             marker=dict(symbol="diamond", size=13, color=T.ACCENT),
                             hovertemplate="%{x:.0%} (%{customdata})<extra>Scenario</extra>"))
    for thr, color, name in ((sc.warning_threshold, T.STATUS["warning"], "Warning"),
                             (sc.critical_threshold, T.STATUS["serious"], "Critical"),
                             (1.0, T.STATUS["critical"], "Capacity")):
        fig.add_vline(x=thr, line=dict(color=color, width=1.2, dash="dot"))
        _threshold_key(fig, f"{name} {thr:.0%}", color)
    xmax = max(1.1, float(max(b.max(), s.max())) * 1.08)
    _layout(fig, None, 380)
    _legend_below(fig, 130)
    fig.update_layout(hovermode="y unified", margin=dict(t=16))
    fig.update_xaxes(tickformat=".0%", range=[0, xmax], showspikes=False,
                     title=dict(text=f"Highest utilization at month {h} (any resource)", font=dict(size=12)))
    fig.update_yaxes(showgrid=False, rangemode=None)
    return fig


def _project_label(r) -> str:
    return f"#{r.rank} {r.site_id} {RES_SHORT[r.resource]}"


def capex_ladder(projects: pd.DataFrame, budget_usd: float | None) -> go.Figure:
    """Cumulative new-project capex in funding order (funded first), against the budget line."""
    p = pd.concat([projects[projects["funded"]], projects[~projects["funded"]]])
    labels = [_project_label(r) for r in p.itertuples(index=False)]
    cost = p["cost_usd"].to_numpy(float) / 1e6
    start = np.concatenate([[0.0], np.cumsum(cost)[:-1]])
    fig = go.Figure()
    for funded, name, color in ((True, "Funded" if budget_usd is not None else "Proposed (no budget cap)", T.ACCENT),
                                (False, "Exceeds budget", _rgba(T.NEUTRAL, 0.55))):
        m = (p["funded"] == funded).to_numpy()
        if not m.any():
            continue
        fig.add_trace(go.Bar(
            y=np.array(labels)[m], x=cost[m], base=start[m], orientation="h", name=name,
            marker=dict(color=color, line=dict(width=0)), width=0.62,
            customdata=np.column_stack([start[m] + cost[m], p["priority_score"].to_numpy(float)[m]]),
            hovertemplate="$%{x:,.1f}M · cumulative $%{customdata[0]:,.1f}M · priority %{customdata[1]:.0f}<extra>%{y}</extra>",
        ))
    if budget_usd is not None:
        fig.add_vline(x=budget_usd / 1e6, line=dict(color=LINEC, width=2, dash="dash"))
        fig.add_annotation(x=budget_usd / 1e6, y=1, yref="paper", yanchor="bottom", showarrow=False, xanchor="left",
                           xshift=4, text=f"budget ${budget_usd / 1e6:,.0f}M", font=dict(size=11))
    _layout(fig, None, 26 * len(p) + 110)
    _legend_below(fig, 190)
    fig.update_layout(hovermode="closest", margin=dict(t=24), barmode="overlay")
    fig.update_xaxes(title=dict(text="Cumulative new-project capex ($M)", font=dict(size=12)), tickprefix="$",
                     showspikes=False)
    fig.update_yaxes(categoryorder="array", categoryarray=labels, autorange="reversed", showgrid=False, rangemode=None)
    return fig


def project_timeline(projects: pd.DataFrame, horizon: int) -> go.Figure:
    """Expansion timing: lead-time window (commit-by -> live), need month and exhaustion if not built."""
    p = projects.sort_values("rank")
    labels = [_project_label(r) for r in p.itertuples(index=False)]
    end_m = int(max(C.MAX_HORIZON, p["activation_month"].max()))
    fig = go.Figure()
    for funded, name, color in ((True, "Lead time: commit-by → live", T.ACCENT),
                                (False, "Lead time (exceeds budget)", _rgba(T.NEUTRAL, 0.7))):
        xs, ys = [], []
        for r, lab in zip(p.itertuples(index=False), labels):
            if bool(r.funded) != funded:
                continue
            xs += [_mdate(max(r.commit_by_month, 0)), _mdate(r.activation_month), None]
            ys += [lab, lab, None]
        if xs:
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=name, line=dict(color=color, width=7),
                                     hoverinfo="skip"))
    over = p[p["commit_by_month"] < 0]
    if len(over):
        fig.add_trace(go.Scatter(
            x=[_mdate(0)] * len(over), y=[_project_label(r) for r in over.itertuples(index=False)], mode="markers",
            name="Commit-by already passed", marker=dict(symbol="triangle-left", size=11, color=T.NEUTRAL),
            customdata=-over["commit_by_month"],
            hovertemplate="commit-by passed %{customdata} mo ago<extra>%{y}</extra>"))
    fig.add_trace(go.Scatter(
        x=[_mdate(m) for m in p["need_month"]], y=labels, mode="markers",
        name=f"{T.RISK_ICONS['Medium']} Warning reached (need)",
        marker=dict(symbol="triangle-up", size=11, color=T.STATUS["warning"], line=dict(width=1, color=LINEC)),
        hovertemplate="need: %{x|%b %Y}<extra>%{y}</extra>"))
    ex = p.dropna(subset=["exhaustion_if_not_built"])
    if len(ex):
        fig.add_trace(go.Scatter(
            x=[_mdate(m) for m in ex["exhaustion_if_not_built"]],
            y=[_project_label(r) for r in ex.itertuples(index=False)],
            mode="markers", name=f"{T.RISK_ICONS['Critical']} Runs out if not built",
            marker=dict(symbol="square", size=10, color=T.STATUS["critical"], line=dict(width=1, color=LINEC)),
            hovertemplate="runs out: %{x|%b %Y}<extra>%{y}</extra>"))
    fig.add_trace(go.Scatter(
        x=[_mdate(m) for m in p["activation_month"]], y=labels, mode="markers", name="Live",
        marker=dict(symbol="circle", size=9, color=T.ACCENT, line=dict(width=1.5, color=LINEC)),
        customdata=np.column_stack([p["lead_time_months"], p["commit_by_month"]]),
        hovertemplate="live: %{x|%b %Y} · lead %{customdata[0]} mo · commit-by M%{customdata[1]}<extra>%{y}</extra>"))
    if horizon < end_m:
        fig.add_vline(x=_mdate(horizon), line=dict(color=LINEC, width=1.2, dash="dot"))
        fig.add_annotation(x=_mdate(horizon), y=1, yref="paper", yanchor="bottom", showarrow=False, xanchor="left",
                           xshift=4, text=f"horizon · {horizon} mo", font=dict(size=11))
    _layout(fig, None, 26 * len(p) + 130)
    _legend_below(fig, 205)
    fig.update_layout(hovermode="closest", margin=dict(t=24))
    fig.update_xaxes(range=[_mdate(0) - pd.Timedelta(days=20), _mdate(end_m) + pd.Timedelta(days=20)],
                     showspikes=False)
    fig.update_yaxes(categoryorder="array", categoryarray=labels, autorange="reversed", showgrid=False, rangemode=None)
    return fig


def footprint_bars(fp: pd.DataFrame, horizon: int) -> go.Figure:
    """Annual emissions run-rate per site: today (bar) vs end of horizon (marker)."""
    f = fp.iloc[::-1]
    labels = f["site_id"] + " " + f["site_name"]
    fig = go.Figure()
    fig.add_trace(go.Bar(y=labels, x=f["annual_emissions_tco2e"] / 1000, orientation="h", name="Today (actual draw)",
                         marker=dict(color=T.ACCENT, line=dict(width=0)), width=0.5,
                         customdata=f["grid_carbon_t_per_mwh"],
                         hovertemplate="%{x:,.1f} kt/yr · grid %{customdata:.2f} t/MWh<extra>Today</extra>"))
    fig.add_trace(go.Scatter(y=labels, x=f["annual_emissions_tco2e_h"] / 1000, mode="markers",
                             name=f"M{horizon} (projected)", marker=dict(symbol="diamond", size=12, color=T.NEUTRAL),
                             hovertemplate="%{x:,.1f} kt/yr<extra>Month " + str(horizon) + "</extra>"))
    _layout(fig, None, 340)
    _legend_below(fig, 170)
    fig.update_layout(hovermode="y unified", margin=dict(t=16), bargap=0.4)
    fig.update_xaxes(title=dict(text="Scope 2 emissions, location-based (kt CO₂e / yr)", font=dict(size=12)),
                     showspikes=False)
    fig.update_yaxes(showgrid=False, rangemode=None)
    return fig
