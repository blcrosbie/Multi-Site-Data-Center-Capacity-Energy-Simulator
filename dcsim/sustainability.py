"""Energy, carbon and water view of a forecast (the "energy" half of the simulator).

These are run-rate estimates: the facility draw in a given month, held flat for a
year. They inform the sustainability discussion; they do not constrain capacity.

    annual energy (MWh)      = facility MW x 8,760 h
    IT energy (MWh)          = annual energy / PUE
    emissions (tCO2e / yr)   = annual energy x grid intensity      (location-based Scope 2, average rate)
    water (million L / yr)   = IT energy x 1,000 kWh/MWh x WUE / 1e6
    cooling (tons)           = cooling load MWt x 284.345
"""

from __future__ import annotations

import pandas as pd

from . import config as C
from .forecast import ForecastResult, period_label

P = C.RESOURCES.index("power")
CL = C.RESOURCES.index("cooling")


def footprint(result: ForecastResult, month: int = 0) -> pd.DataFrame:
    """Annualised energy, emissions and water per site at the draw of `month`."""
    s = result.sites
    mw = result.demand[:, P, month]
    it_mw = mw / s["pue"].to_numpy(float)
    energy = mw * C.HOURS_PER_YEAR
    it_energy = it_mw * C.HOURS_PER_YEAR
    return pd.DataFrame({
        "site_id": s["site_id"],
        "site_name": s["site_name"],
        "region": s["region"],
        "month": month,
        "period": period_label(month),
        "facility_mw": mw,
        "it_mw": it_mw,
        "pue": s["pue"],
        "annual_energy_mwh": energy,
        "grid_carbon_t_per_mwh": s["grid_carbon_t_per_mwh"],
        "annual_emissions_tco2e": energy * s["grid_carbon_t_per_mwh"].to_numpy(float),
        "wue_l_per_kwh": s["wue_l_per_kwh"],
        "annual_water_ml": it_energy * 1000 * s["wue_l_per_kwh"].to_numpy(float) / 1e6,
        "cooling_tons": result.demand[:, CL, month] * C.MWT_TO_TONS,
    })


def footprint_now_vs_horizon(result: ForecastResult) -> pd.DataFrame:
    """One row per site: today's run-rate next to the run-rate at the end of the horizon."""
    now, later = footprint(result, 0), footprint(result, result.horizon)
    keep = ["annual_energy_mwh", "annual_emissions_tco2e", "annual_water_ml", "facility_mw"]
    out = now[["site_id", "site_name", "region", "pue", "grid_carbon_t_per_mwh", "wue_l_per_kwh"] + keep].copy()
    for c in keep:
        out[f"{c}_h"] = later[c].to_numpy()
    return out


def carbon_contrast(result: ForecastResult) -> dict | None:
    """The cleanest vs dirtiest site at today's draw: how much more power, how much more carbon."""
    f = footprint(result, 0)
    lo = f.loc[f["grid_carbon_t_per_mwh"].idxmin()]
    hi = f.loc[f["grid_carbon_t_per_mwh"].idxmax()]
    if lo.site_id == hi.site_id or lo.annual_emissions_tco2e <= 0:
        return None
    return {
        "clean": lo.to_dict(), "dirty": hi.to_dict(),
        "power_ratio": hi.facility_mw / lo.facility_mw,
        "emissions_ratio": hi.annual_emissions_tco2e / lo.annual_emissions_tco2e,
    }
