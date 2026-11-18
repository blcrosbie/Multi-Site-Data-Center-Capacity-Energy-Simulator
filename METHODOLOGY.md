# Calculation Methodology

> **Synthetic data — demonstration only.** This is a decision-support demonstration, not an engineering
> or financial model. Costs and lead times are order-of-magnitude illustrations.

All constants referenced below live in [`dcsim/config.py`](dcsim/config.py); every one can be overridden per scenario.

## 1. Time base

| Item | Definition |
|---|---|
| Baseline ("actual") | Synthetic snapshot as of **end of October 2026** = month 0 |
| Forecast months | 1 … 36, monthly (month 1 = Nov 2026) |
| Display horizon | User-selected 6, 12, 18, 24, 30 or 36 months; controls charts, tables, sizing window |
| Risk lookahead | Always 36 months, so a site's risk status does not change when the display horizon changes |

Month 0 is labelled `Actual (synthetic baseline)` in every table and export; months ≥ 1 are `Projected`.

## 2. Resources and units

| Resource | Demand | Capacity | Unit |
|---|---|---|---|
| Electrical power | Total facility draw (IT load × PUE) | Usable facility electrical capacity | MW |
| Cooling | Heat rejected to the cooling plant | Installed cooling plant capacity (N, not N+1) | MWt (MW thermal); 1 MWt = 284.3 tons of refrigeration |
| Rack space | Occupied racks | Installed rack positions | racks |

Cooling load is tied to IT load at data generation: `cooling load = (power demand / PUE) × 1.04`.
Nearly all IT power becomes heat; the 4% covers in-room losses (PDUs, lighting, fans).

## 3. Core formulas (SOW §6)

| Quantity | Formula |
|---|---|
| Utilization | `demand / capacity` |
| Headroom | `capacity − demand` |
| Forecast demand (power, cooling) | `baseline × (1 + g) ^ (m / 12) × (1 − e)` for m ≥ 1 |
| Forecast demand (racks) | `baseline × ((1 + g) / (1 + d)) ^ (m / 12)` |
| Capacity in month m | `baseline capacity + Σ expansions with activation_month ≤ m` |
| Exhaustion month | first month m where `demand > capacity` (strictly greater) |
| Threshold month | first month m (including 0) where `utilization ≥ threshold` |
| Portfolio utilization | `Σ demand / Σ capacity` across the six sites |

Where `g` = annual growth, `e` = efficiency improvement, `d` = rack density growth.

**Growth resolution:** site growth from data → replaced by the portfolio-wide growth if set → replaced by a site-specific override if set.

**Efficiency:** reduces modeled power and cooling demand from month 1 onward. Month 0 is the measured baseline and is never altered. Efficiency does not change rack count.

**Rack densification (`d`, default 3%/yr):** new load lands at higher kW per rack, so rack demand grows more slowly than power demand. Set `d = 0` to reproduce the SOW formula exactly for racks.

## 4. Constraint identification

- **First limiting resource (per site):** the resource with the earliest exhaustion month. If none exhausts within 36 months, the earliest warning-threshold breach. If none breaches, the resource with the highest utilization at the horizon. Ties go to the higher horizon utilization.
- **Earliest site constraint (portfolio):** the earliest site-level exhaustion across all sites and resources (or earliest warning breach if nothing exhausts). This is shown prominently on the dashboard. **Portfolio-level exhaustion is reported separately and never substitutes for it.** In the baseline, the portfolio as a whole never runs out of power within 36 months, yet DC3 does in month 8.

## 5. Site risk status (rules-based)

Evaluated in order; first match wins. Thresholds default to warning 80%, critical 90%.

| Status | Rule |
|---|---|
| **Critical** | any resource exhausted within 6 months |
| **High** | any resource exhausted within 12 months, **or** any resource currently ≥ critical threshold |
| **Medium** | any resource exhausted within 24 months, **or** warning threshold reached within 12 months (including now) |
| **Low** | otherwise |

Each site carries a `risk_reason` string naming the rule that fired. **Portfolio risk** = the worst site status (a conservative choice: one critical site makes the portfolio critical).

## 6. Capital planning (SOW Module 5)

**Sizing.** For each site and resource, the smallest addition that keeps utilization ≤ the warning threshold in *every* month of the horizon:

```
shortfall = max over m ≤ horizon of ( demand_m / warning_threshold − capacity_m )
capacity_added = ceil(shortfall / increment) × increment      (0.5 MW, 0.5 MWt, 10 racks)
```

**Timing.** `need month` = first month utilization ≥ warning threshold. `commit by` = need month − lead time
(power 12, cooling 9, racks 3 months). Activation = `max(need month, lead time)`. If need month < lead time the
project **arrives late** and the site runs above target until it lands.

**Cost.** `capacity_added × unit cost`, with defaults of $3.5M/MW electrical, $1.8M/MWt cooling and $20k/rack fit-out.
These are illustrative. No ROI is claimed.

**Ranking.** Each score is 0–100:

| Score | Formula | Weight |
|---|---|---|
| Urgency | `100 × (1 − slack / 24)`, clipped to 0–100; slack = commit-by month | 0.5 |
| Risk | site status: Low 0 · Medium 33 · High 67 · Critical 100 | 0.3 |
| Cost | `100 × cheapest candidate cost / this cost` | 0.2 |

`priority = 0.5·urgency + 0.3·risk + 0.2·cost`; rank descending (ties → earlier need month).

**Budget.** Projects are funded in rank order while they fit in the remaining budget. A project that doesn't fit is
skipped and flagged "exceeds available budget", and cheaper lower-ranked projects may still be funded. Funded projects
are applied as expansions and the forecast is re-run to report headroom after investment. Already-planned expansions
are treated as committed capex and sit outside the new-project budget.

## 7. Monte Carlo risk layer (extension beyond the SOW)

The deterministic engine above produces the official numbers. The Monte Carlo layer runs **the same `project()`
function** across N draws (default 2,000, seed 42), sampling:

| Uncertainty | Distribution | Default |
|---|---|---|
| Annual growth | `g_site + σ × (√ρ·Z_common + √(1−ρ)·Z_site)` | σ = 4 pts, ρ = 0.5 |
| Expansion slip | `round(Triangular(0, mode, max))` months added to each activation | mode 1, max 9 |
| Efficiency realised | `e × Uniform(low, 1)` | low = 0.5 |

The common shock `Z_common` makes sites move together (a regional demand surge hits everyone).
Slip is never negative (projects rarely land early). Outputs are P10/P50/P90 exhaustion months and P(exhaustion
within 6/12/…/36 months) per site and per resource. Quantiles use the empirical inverse CDF, so "not exhausted within
36 months" is a valid outcome rather than an interpolation artefact.

**Consistency guarantee:** with σ = 0, slip = 0 and full efficiency realisation, every draw reproduces the deterministic
forecast exactly (tested in `tests/test_montecarlo_and_data.py`).

## 8. Documented test cases

| ID | Inputs | Expected | Test |
|---|---|---|---|
| TC-1 | 10 MW demand, 12%/yr | month 24 = 10 × 1.12² = **12.544 MW** | `test_case_1_compound_growth_formula` |
| TC-2 | 8 MW on 10 MW | utilization **80%**, headroom **2 MW** | `test_case_2_utilization_and_headroom` |
| TC-3 | 8 MW on 10 MW, 25%/yr | month 12 = exactly 10 MW (not exceeding) → exhaustion **month 13 (Nov 2027)** | `test_case_3_exhaustion_is_first_month_demand_exceeds_capacity` |
| TC-4 | TC-3 plus +5 MW at month 10 | capacity 10 → 15 at month 10; exhaustion **month 34** (12·ln 1.875 / ln 1.25 = 33.8) | `test_case_4_planned_expansion_activates_on_its_month` |
| TC-5 | TC-3, 12-mo horizon, 80% target | peak 10 MW → need 12.5 MW → **+2.5 MW, $8.75M**, arrives late | `test_project_sizing_hand_case` |
| TC-6 | Real dataset, baseline | portfolio never exhausts power; earliest constraint = **DC3 power** | `test_site_constraint_not_hidden_by_portfolio` |

Run them with `pytest -q`.

## 9. Limitations

- Growth is smooth compound growth; real demand arrives in lumpy customer deployments.
- Power, cooling and racks are independent constraints. There is no modeling of redundancy topology (N+1, 2N),
  stranded capacity, or per-hall distribution limits.
- Expansion capacity is assumed fully usable on its activation date, with no commissioning ramp.
- Unit costs are flat and ignore economies of scale, site conditions, inflation and financing.
- Efficiency is applied uniformly to power and cooling. In practice PUE improvements mostly reduce cooling and
  electrical overhead, not IT load.
- Monte Carlo distributions are illustrative priors, not fitted to historical data.

## 10. Decision support rules (SOW Module 6)

The Executive Decisions page groups rules-based recommendations into the six SOW categories. Every recommendation
carries a "Based on" line naming its metric and rule. None of the analyses below change the official forecast or
the capital ranking; assumptions marked *illustrative* appear only in recommendation text.

| Category | Rule |
|---|---|
| Immediate attention | Projects whose commit-by month ≤ 0, grouped by site; months still over capacity after the plan are stated |
| Facilities at risk | Every site not at Low risk: warning / critical / exhaustion dates per resource, the rule that fired, Monte Carlo P(exhaustion ≤ 36 mo), and status after the plan |
| Expansion timing | Late projects that go live after their site runs out: the gap in months, and the gap if the schedule is **crashed** (lead × (1 − 23%), rounded up; *illustrative*). Upcoming commit-by dates |
| Optimization | **Deferrable planned spend**: the forecast re-run without each planned expansion; if that site/resource stays below the warning threshold for 36 months it is a deferral candidate, with the present value of a 12-month deferral at 10% (*illustrative*). **PUE**: sites at PUE ≥ 1.50 brought to 1.35 (*illustrative*) free `facility MW × (1 − 1.35 / PUE)` without changing IT heat. **Stranded power**: rack-bound sites with power below the warning line. **Load placement**: for a site still over power or cooling capacity after the plan, the IT MW needed through the gap window, and other sites (no gap of their own, cleaner per MWh of IT) that can absorb it while staying under the warning threshold on power **and** cooling for the whole window |
| Over budget | Each unfunded project with its exhaustion month and months over capacity if not built, and its shortfall against the unallocated budget |
| Deferral consequences | Each recommended project deferred, and each planned expansion slipped, by N months (default 6): extra months over capacity and above the warning line over the 36-month lookahead, before new projects |

**Emissions for load placement** = IT MWh × PUE × average location-based grid intensity. Marginal emission rates
(the plant that actually ramps for new load) may narrow the gap between sites. Network capacity and latency are not
modeled, so the recommendation says to confirm backhaul first.

**Monte Carlo attribution.** Scenario Planning also runs the simulation with zero schedule slip. When slip adds
≥ 5 points to P(exhaustion ≤ 24 months), near-term risk is attributed to schedule slip; otherwise to demand growth.
Example (baseline): DC2 power is 19% within 24 months as simulated but 0.4% with no slip, while its 28% within 36
months is mostly demand outgrowing even the Phase 3 capacity (22% with no slip).

**Energy, carbon and water run-rates** (Capacity Forecasting): annual energy = facility MW × 8,760 h; emissions =
energy × grid intensity (location-based Scope 2); water = IT energy × WUE. They inform siting and are not capacity
constraints.

## 11. Exports (SOW Module 7)

| File | Page | Contents |
|---|---|---|
| Monthly capacity forecast (CSV) | Capacity Forecasting | Every site × resource × month through the horizon; `data_type` = actual (month 0) or projected; threshold flags |
| Facility-level capacity data (CSV) | Capacity Forecasting | One row per site × resource: today, horizon, warning / critical / exhaustion months, first limiting resource, risk |
| Scenario assumptions & results (CSV) | Scenario Planning | Long format `section, site_id, item, value, unit`: every assumption, portfolio KPIs, site results, capital totals |
| Scenario comparison (CSV) | Scenario Planning | The five presets (plus Custom) side by side |
| Capital investment comparison (CSV) | Executive Decisions | Every sized project with scores, rank, timing, funding and headroom after investment |
| Deferral analysis (CSV) | Executive Decisions | The deferral-consequences table for the chosen delay |
| Executive summary (Markdown) | Executive Decisions | Headline, KPIs, every recommendation with its basis, ranked investments, briefing notes |

CSV files are UTF-8 with a byte-order mark so Excel opens them cleanly.
