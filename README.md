# Multi-Site Data Center Capacity & Energy Simulator

## Overview
This application is a full-stack capacity planning simulator built with Python and Streamlit. It models the physical and energy constraints of six synthetic data center campuses, forecasting when each site will run out of electrical power (MW), thermal cooling (MWt), or physical rack space.

Rather than relying on deterministic, single-date guesses, the engine utilizes Monte Carlo simulation to project correlated AI load growth against utility lead times, translating standard P50 estimates into probabilistic P10 exhaustion dates. It also models grid carbon intensity (eGRID), the water-energy nexus (WUE vs. PUE), and capital expansion logic, acting as a bridge between data center infrastructure management and utility-scale power systems engineering.

It forecasts power, cooling and rack-space demand over 6–36 months, identifies the earliest site-level constraint, sizes and
ranks expansion projects against a capital budget, and adds a **Monte Carlo risk layer** that turns single exhaustion
dates into probabilities (the single-path forecast says DC2 never runs out of power; Monte Carlo puts it at 28% within
36 months, and almost all of the next-two-years risk is its Phase 3 expansion landing late).

> **Synthetic data — demonstration only.** No real facility, employer or infrastructure data is used.

Plain-language field guide (lexicon + sustainability crossover): [GUIDE.md](GUIDE.md) · Scope and requirements: [TASK.md](TASK.md) · Methodology: [METHODOLOGY.md](METHODOLOGY.md) ·
Data dictionary: [DATA_DICTIONARY.md](DATA_DICTIONARY.md)

## Status

| Milestone | Scope | State |
|---|---|---|
| 1 | Architecture, synthetic dataset, calculation engine, tests, methodology | ✅ done |
| 2 | Streamlit shell (Campbell PowerShell dark theme + light mode, 4-page nav, scenario presets, reset) + Executive Overview + facility comparison | ✅ done |
| 3 | Capacity Forecasting + Scenario Planning pages (all SOW levers, preset comparison, Monte Carlo view) | ✅ done |
| 4 | Capital planning + Executive Decisions (rules engine, six SOW recommendation categories) | ✅ done |
| 5 | Exports ✅ (CSV + Markdown executive summary) · deployment to Streamlit Community Cloud · walkthrough | ⏳ |

## The four pages

| Page | What it answers |
|---|---|
| **Executive Overview** | Portfolio KPIs, the earliest site-level constraint, six-site comparison, headline insights |
| **Capacity Forecasting** | Per site and resource: demand vs capacity with a Monte Carlo band, what fills up first, a "show the math" check of the exhaustion month, a status timeline for all 18 site/resource rows, exhaustion dates with P10/P50/P90, energy / carbon / water run-rates, monthly data and CSV exports |
| **Scenario Planning** | Every lever (portfolio and site growth, efficiency, expansion with "size it for me", budget, unit costs, lead times), impact vs baseline, the five presets side by side, and the Monte Carlo risk layer with slip-vs-growth attribution |
| **Executive Decisions** | This month's capital decision, ranking vs budget, expansion timing, deferral consequences, optimization (deferrable spend, PUE, stranded power, carbon-aware load placement), briefing notes for three audiences, CSV + Markdown exports |

## Quick start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

python -m dcsim.generate          # (re)build data/*.csv - reproducible, seed 2026
pytest -q                         # SOW formulas, validation, scenarios, Monte Carlo, app smoke tests
streamlit run app.py              # http://localhost:8501
```

### Simulation visuals (for decks / screen capture)

```bash
python scripts/animate_simulation.py                              # DC2 power: animated GIF + 1920x1080 PNG
python scripts/animate_simulation.py --site DC5 --resource cooling
python scripts/animate_simulation.py --show                       # live window, for screen recording
python scripts/animate_simulation.py --curves                     # all six sites: P(exhaustion) by month
python scripts/animate_simulation.py --scenario high_growth --site DC3
python scripts/animate_simulation.py --light                      # light-theme version (*_light.png/gif)
```

Files are written to `outputs/` (git-ignored).

## Project layout

```
app.py            Streamlit entry: page config, navigation, shared sidebar, synthetic-data banner
views/            the four pages (overview.py, forecasting.py, scenarios.py, decisions.py)
ui/               presentation only: state.py (levers/presets/context), sidebar.py, components.py, charts.py
.streamlit/       config.toml ([theme.dark] Campbell PowerShell, [theme.light] xterm grays; Ubuntu fonts)
dcsim/
  rules.py        deterministic insights + Executive Decisions panel (each cites its underlying metric)
  decisions.py    crash / deferral / deferrable-spend / PUE / stranded-power / load-placement analysis
  sustainability.py  energy, carbon and water run-rates per site
  export.py       CSV exports and the Markdown executive summary
  config.py       all constants and default assumptions (one place to document/override)
  scenario.py     Scenario + Expansion dataclasses, 5 predefined scenarios, input validation
  data.py         CSV loading + physical-consistency validation
  generate.py     reproducible synthetic dataset generator
  forecast.py     vectorised projection engine, exhaustion/threshold/risk logic, KPIs
  capital.py      project sizing, cost, priority ranking, budget allocation
  montecarlo.py   stochastic growth / schedule slip / efficiency on the same engine
  theme.py        dark/light neutrals + one CVD-checked data palette that reads on both
data/             sites.csv, planned_expansions.csv
scripts/          animate_simulation.py
tests/            pytest suite incl. documented hand-worked test cases
METHODOLOGY.md    formulas, rules and limitations;  DATA_DICTIONARY.md  columns, units, derived fields
GUIDE.md          plain-language field guide; its worked numbers are re-checked in tests/test_decisions.py
```

## Design notes

- **One engine, two modes.** The deterministic forecast is a Monte Carlo run with one draw and no noise, so the
  two can't drift apart. A test asserts that zero-uncertainty Monte Carlo reproduces the deterministic arrays exactly.
- **Site constraints are never hidden by portfolio totals.** In the baseline the portfolio sits at 65% power and
  never exhausts, while DC3 runs out of power in month 8. The earliest site constraint is the headline KPI.
- **Rules, not AI.** Risk status and recommendations come from documented, deterministic rules, and each carries
  the reason that fired.
- **Fast.** 2,000 simulations × 6 sites × 3 resources × 37 months run in under 0.1 s, so the UI can recompute on
  every slider change.
- **Terminal-flavoured styling, dark by default.** The default theme is the Windows Terminal "Campbell
  PowerShell" scheme (navy `#012456`, Campbell ANSI colours); a light mode is one click away in the app menu
  (⋮ → Light / Dark / System). Square 1px-bordered panels and Ubuntu / Ubuntu Mono type in both. Panels tint
  from the active text colour and charts use Streamlit's Plotly theme, so everything follows the switch live.
  One data palette serves both modes: site colours were validated for colour-vision deficiency on navy *and*
  white, and avoid the yellow / orange / red reserved for risk status (Low is neutral gray). Status tags always
  carry a shape and a label as well as colour.
