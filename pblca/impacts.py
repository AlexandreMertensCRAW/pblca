"""Layer 3 — Impacts: climate change characterisation.

Indicators computed from the physical inventory (layer 2), never
modifying it (ISO 14044 requirement: characterisation distinct from
inventory; gases remain tracked separately to allow GWP, GWP*, or any
future indicator):

* **GWP100 / GWP20** (IPCC AR6, Forster et al. 2021):
  CO2 = 1; CH4 = 27.9 (fossil) / 27.0 (biogenic, used here since the
  methane is of agricultural origin) in GWP100; CH4 = 80.8 (biogenic)
  in GWP20; N2O = 273 (GWP100) / 273 (GWP20).
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
from typing import Dict, Optional

from .gases import GasLedger

# IPCC AR6 (Forster et al. 2021, Table 7.15) — biogenic values for CH4.
GWP100_FACTORS: Dict[str, float] = {"CO2": 1.0, "CH4": 27.0, "N2O": 273.0}
GWP20_FACTORS: Dict[str, float] = {"CO2": 1.0, "CH4": 80.8, "N2O": 273.0}

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


def compute_gwp100(ledger: GasLedger) -> float:
    """GWP100 impact (kg CO2e) of the inventory.

    Args:
        ledger: emissions ledger (layer 2).

    Returns:
        kg CO2e (AR6, biogenic CH4 = 27).
    """
    return sum(
        ledger.total(gas) * factor for gas, factor in GWP100_FACTORS.items()
    )


def compute_gwp20(ledger: GasLedger) -> float:
    """GWP20 impact (kg CO2e) of the inventory.

    Returns:
        kg CO2e (AR6, biogenic CH4 = 80.8).
    """
    return sum(ledger.total(gas) * factor for gas, factor in GWP20_FACTORS.items())


def compute_gwpstar(ledger: GasLedger, inputs: GwpStarInputs) -> float:
    """GWP* impact (kg CO2-we, Cain et al. 2019).

    CO2-we = 4.53 × ΔE_CH4 + 0.28 × E_CH4 + GWP100(CO2) + GWP100(N2O).
    Long-lived gases (CO2, N2O) enter as "stock" via their GWP100.

    Args:
        ledger: emissions ledger.
        inputs: CH4 time series (current and t−Δt).

    Returns:
        kg CO2-warming-equivalent of the year.
    """
    ch4 = ledger.total("CH4")
    co2 = ledger.total("CO2") * GWP100_FACTORS["CO2"]
    n2o = ledger.total("N2O") * GWP100_FACTORS["N2O"]
    flow = GWPSTAR_FLOW_FACTOR * inputs.delta_e()
    stock_ch4 = GWPSTAR_STOCK_FACTOR * ch4
    return flow + stock_ch4 + co2 + n2o


def characterize(ledger: GasLedger, gwpstar_inputs: Optional[GwpStarInputs] = None) -> Dict[str, float]:
    """Compute all impact indicators of an inventory.

    Args:
        ledger: emissions ledger (layer 2).
        gwpstar_inputs: CH4 series for GWP* (default: farm at equilibrium).

    Returns:
        a {"gwp100": ..., "gwp20": ..., "gwpstar": ...} dictionary
        in kg CO2e / kg CO2-we per year.
    """
    if gwpstar_inputs is None:
        gwpstar_inputs = GwpStarInputs(ch4_current_kg=ledger.total("CH4"))
    return {
        "gwp100": compute_gwp100(ledger),
        "gwp20": compute_gwp20(ledger),
        "gwpstar": compute_gwpstar(ledger, gwpstar_inputs),
    }
