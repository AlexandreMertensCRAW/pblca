"""Layer 3 — Impacts: climate change characterisation.

Indicators computed from the physical inventory (layer 2), never
modifying it (ISO 14044 requirement: characterisation distinct from
inventory; gases remain tracked separately to allow GWP, GWP*, or any
future indicator):

* **GWP100 / GWP20** (IPCC AR6, Forster et al. 2021, Table 7.15,
  no climate-carbon feedbacks):
  CO2 = 1; CH4 = 29.8 (fossil) / 27.0 (non-fossil, used here since
  the methane is of agricultural origin) in GWP100; CH4 = 82.5
  (fossil) / 79.7 (non-fossil) in GWP20; N2O = 273 (GWP100 and GWP20).
  The published likely (5-95 %) ranges are carried by the
  corresponding parameters of :mod:`pblca.params`
  (``gwp100_ch4_biogenic`` ... ``gwp20_n2o``) and propagated by the
  Monte-Carlo; the constants below are the deterministic fallback.
* **GWP*** (Cain et al. 2019, eq. 7): CO2-we = 4.0 × ΔE_CH4 + 0.28 × E_CH4
  where ΔE is the change in CH4 emissions over the last 20 years and
  E the current emission; long-lived gases (CO2, N2O) are counted in
  stock: GWP* = 4.53 × ΔE + 0.28 × E with GWP100=27
  (r/s factors from Cain et al. 2019; default Δt = 20 years).

Choosing ΔE = 0 (constant CH4 emissions) gives GWP* = 0.28 × E_CH4:
a farm with stable methane has a small marginal warming impact —
exactly the property of the indicator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional

from .gases import GasLedger

# IPCC AR6 (Forster et al. 2021, Table 7.15) — biogenic values for CH4.
GWP100_FACTORS: Dict[str, float] = {"CO2": 1.0, "CH4": 27.0, "N2O": 273.0}
GWP20_FACTORS: Dict[str, float] = {"CO2": 1.0, "CH4": 79.7, "N2O": 273.0}

# GWP* (Cain et al. 2019): CO2-we = 4.53×ΔE + 0.28×E (GWP100=27, dt=20 yr)
GWPSTAR_FLOW_FACTOR = 4.53  # × ΔE_CH4 (change over Δt)
GWPSTAR_STOCK_FACTOR = 0.28  # × E_CH4 (current emission)


@dataclass
class GwpStarInputs:
    """Time-series inputs of GWP* (methane).

    Attributes:
        ch4_current_kg: CH4 emissions of the simulated year (kg).
        ch4_previous_kg: CH4 emissions of year t−Δt (kg). Defaults to
            the current year (farm at equilibrium → ΔE = 0).
        dt_years: change horizon Δt (default 20 years, Cain et al. 2019).
    """

    ch4_current_kg: float
    ch4_previous_kg: Optional[float] = None
    dt_years: float = 20.0

    def delta_e(self) -> float:
        """Per-year change in CH4 emission over Δt: (E_t − E_{t−Δt})/Δt."""
        prev = self.ch4_current_kg if self.ch4_previous_kg is None else self.ch4_previous_kg
        return (self.ch4_current_kg - prev) / self.dt_years


def compute_gwp100(
    ledger: GasLedger, values: Optional[Mapping[str, float]] = None
) -> float:
    """GWP100 impact (kg CO2e) of the inventory.

    Args:
        ledger: emissions ledger (layer 2).
        values: drawn (or central) parameter values; the character-
            isation factors ``gwp100_ch4_biogenic`` and
            ``gwp100_n2o`` are read from it when present, so their
            uncertainty propagates through the Monte-Carlo (CO2 = 1 by
            definition). Defaults to the AR6 constants.

    Returns:
        kg CO2e (AR6, non-fossil CH4).
    """
    factors = dict(GWP100_FACTORS)
    if values:
        if "gwp100_ch4_biogenic" in values:
            factors["CH4"] = values["gwp100_ch4_biogenic"]
        if "gwp100_n2o" in values:
            factors["N2O"] = values["gwp100_n2o"]
    return sum(
        ledger.total(gas) * factor for gas, factor in factors.items()
    )


def compute_gwp20(
    ledger: GasLedger, values: Optional[Mapping[str, float]] = None
) -> float:
    """GWP20 impact (kg CO2e) of the inventory.

    Args:
        ledger: emissions ledger (layer 2).
        values: drawn (or central) parameter values; the character-
            isation factors ``gwp20_ch4_biogenic`` and ``gwp20_n2o``
            are read from it when present (same mechanism as
            :func:`compute_gwp100`).

    Returns:
        kg CO2e (AR6, non-fossil CH4).
    """
    factors = dict(GWP20_FACTORS)
    if values:
        if "gwp20_ch4_biogenic" in values:
            factors["CH4"] = values["gwp20_ch4_biogenic"]
        if "gwp20_n2o" in values:
            factors["N2O"] = values["gwp20_n2o"]
    return sum(ledger.total(gas) * factor for gas, factor in factors.items())


def compute_gwpstar(
    ledger: GasLedger,
    inputs: GwpStarInputs,
    values: Optional[Mapping[str, float]] = None,
) -> float:
    """GWP* impact (kg CO2-we, Cain et al. 2019).

    CO2-we = 4.53 × ΔE_CH4 + 0.28 × E_CH4 + GWP100(CO2) + GWP100(N2O).
    Long-lived gases (CO2, N2O) enter as "stock" via their GWP100.

    Args:
        ledger: emissions ledger.
        inputs: CH4 time series (current and t−Δt).

    Returns:
        kg CO2-warming-equivalent of the year.
    """
    n2o_factor = GWP100_FACTORS["N2O"]
    if values and "gwp100_n2o" in values:
        n2o_factor = values["gwp100_n2o"]
    ch4 = ledger.total("CH4")
    co2 = ledger.total("CO2") * GWP100_FACTORS["CO2"]
    n2o = ledger.total("N2O") * n2o_factor
    flow = GWPSTAR_FLOW_FACTOR * inputs.delta_e()
    stock_ch4 = GWPSTAR_STOCK_FACTOR * ch4
    return flow + stock_ch4 + co2 + n2o


def characterize(ledger: GasLedger, gwpstar_inputs: Optional[GwpStarInputs] = None, values: Optional[Mapping[str, float]] = None ) -> Dict[str, float]:
    """Compute all impact indicators of an inventory.

    Args:
        ledger: emissions ledger (layer 2).
        gwpstar_inputs: CH4 series for GWP* (default: farm at equilibrium).
        values: drawn (or central) parameter values carrying the
            characterisation factors (``gwp100_ch4_biogenic``,
            ``gwp20_ch4_biogenic``, ``gwp100_n2o``, ``gwp20_n2o``);
            defaults to the AR6 constants.

    Returns:
        a {"gwp100": ..., "gwp20": ..., "gwpstar": ...} dictionary
        in kg CO2e / kg CO2-we per year.
    """
    if gwpstar_inputs is None:
        gwpstar_inputs = GwpStarInputs(ch4_current_kg=ledger.total("CH4"))
    return {
        "gwp100": compute_gwp100(ledger, values),
        "gwp20": compute_gwp20(ledger, values),
        "gwpstar": compute_gwpstar(ledger, gwpstar_inputs, values),
    }
