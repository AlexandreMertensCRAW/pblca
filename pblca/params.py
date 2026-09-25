"""Transversal layer: traceable parameters with uncertainties.

Each parameter or emission factor used by the engine carries:
  - a central value,
  - an uncertainty distribution (normal, lognormal, triangular, uniform),
  - the exact bibliographic reference the value comes from.

ISO 14044 requirement (section 4.2.3, sensitivity and uncertainty analysis):
parameter uncertainties are propagated by Monte-Carlo (see
:mod:`pblca.engine`). A given parameter (identified by its ``pid``)
receives THE SAME drawn value for all its occurrences within a single
Monte-Carlo iteration — including across farms (farm 1 and farm 2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

# Pseudo-random generator injected everywhere (reproducibility of draws).
import numpy as _np


@dataclass(frozen=True)
class Reference:
    """Bibliographic reference a value originates from.

    Attributes:
        source: short description of the source (e.g. "IPCC 2006, Vol.4 Ch.10, Eq. 10.6").
        doi_or_url: DOI or URL when available.
        detail: optional precision (table, page, published uncertainty).
    """

    source: str
    doi_or_url: str = ""
    detail: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {"source": self.source, "doi_or_url": self.doi_or_url, "detail": self.detail}


@dataclass
class Parameter:
    """Traceable parameter or emission factor.

    Attributes:
        pid: unique identifier (cross-farm consistency key in Monte-Carlo).
        value: central value used for the deterministic scenario.
        unit: physical unit.
        distribution: distribution name ("normal", "lognormal",
            "triangular", "uniform" or "none").
        sd: standard deviation (normal / lognormal as geometric sigma).
        min_/max_: bounds (triangular / uniform).
        reference: bibliographic reference of the value.
        description: free description.
    """

    pid: str
    value: float
    unit: str
    distribution: str = "none"
    sd: float = 0.0
    min_: Optional[float] = None
    max_: Optional[float] = None
    reference: Optional[Reference] = None
    description: str = ""

    def __post_init__(self) -> None:
        if self.distribution not in ("none", "normal", "lognormal", "triangular", "uniform"):
            raise ValueError(
                f"Parameter {self.pid}: unknown distribution '{self.distribution}'"
            )

    def sample(self, rng: "_np.random.Generator") -> float:
        """Draw a value from the uncertainty distribution.

        The same ``rng`` instance and the same ``pid`` guarantee
        cross-usage consistency: the engine calls ``sample`` only once per
        parameter and per iteration (see ``ParameterSet.draw``).
        """
        if self.distribution == "none":
            return float(self.value)
        if self.distribution == "normal":
            return float(rng.normal(self.value, self.sd))
        if self.distribution == "lognormal":
            # sd = geometric sigma ; median = central value.
            return float(rng.lognormal(math.log(self.value), self.sd))
        if self.distribution == "triangular":
            return float(rng.triangular(self.min_, self.value, self.max_))
        if self.distribution == "uniform":
            return float(rng.uniform(self.min_, self.max_))
        raise AssertionError(self.distribution)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "value": self.value,
            "unit": self.unit,
            "distribution": self.distribution,
            "sd": self.sd,
            "min": self.min_,
            "max": self.max_,
            "reference": self.reference.as_dict() if self.reference else None,
            "description": self.description,
        }


class ParameterSet:
    """The set of parameters of a simulation.

    Central role for uncertainty consistency: during a Monte-Carlo
    iteration, ``draw`` draws each parameter ONCE; every farm and every
    process using that parameter therefore receives exactly the same
    value (explicit requirement: the same emission factor for farm 1
    and farm 2).
    """

    def __init__(self) -> None:
        self._params: Dict[str, Parameter] = {}

    def add(
        self,
        pid: str,
        value: float,
        unit: str,
        distribution: str = "none",
        sd: float = 0.0,
        min_: Optional[float] = None,
        max_: Optional[float] = None,
        reference: Optional[Reference] = None,
        description: str = "",
    ) -> Parameter:
        if pid in self._params:
            raise KeyError(f"Parameter already defined: {pid}")
        p = Parameter(
            pid, value, unit, distribution, sd, min_, max_, reference, description
        )
        self._params[pid] = p
        return p

    def add_p(self, p: Parameter) -> None:
        if p.pid in self._params:
            raise KeyError(f"Parameter already defined: {p.pid}")
        self._params[p.pid] = p

    def get(self, pid: str) -> Parameter:
        try:
            return self._params[pid]
        except KeyError:
            raise KeyError(f"Unknown parameter: {pid}") from None

    def __contains__(self, pid: str) -> bool:
        return pid in self._params

    def __len__(self) -> int:
        return len(self._params)

    def pids(self) -> list[str]:
        return sorted(self._params)

    # ------------------------------------------------------------------
    # Monte-Carlo consistency
    # ------------------------------------------------------------------
    def draw(self, rng: Optional["_np.random.Generator"] = None) -> Dict[str, float]:
        """Draw a complete set of values (one per parameter).

        Args:
            rng: numpy generator (injected for reproducibility).

        Returns:
            a ``pid -> drawn value`` dictionary. A Monte-Carlo iteration
            calls ``draw`` once, then all farms share this dictionary:
            cross-farm consistency guaranteed.
        """
        if rng is None:
            rng = _np.random.default_rng()
        return {pid: p.sample(rng) for pid, p in self._params.items()}

    def central_values(self) -> Dict[str, float]:
        """Set of central values (deterministic scenario)."""
        return {pid: p.value for pid, p in self._params.items()}

    def as_dict(self) -> Dict[str, Any]:
        return {pid: p.as_dict() for pid, p in sorted(self._params.items())}


# ----------------------------------------------------------------------
# Default parameter library (temperate cattle farms).
# All values carry their reference. Uncertainties of IPCC factors
# follow the published confidence intervals (±95 %).
# ----------------------------------------------------------------------
def build_default_parameter_set() -> ParameterSet:
    """Build the engine's default parameter set.

    Returns:
        a complete ParameterSet for a temperate cattle farm.
    """
    ps = ParameterSet()

    # --- Physical constants / conversions ----------------------------
    ps.add(
        "energy_ch4_mj_per_kg", 55.65, "MJ/kg CH4",
        reference=Reference("IPCC 2006, Vol.4 Ch.10 (p.10.29)"),
        description="Energy content of methane (IPCC 2006).",
    )
    ps.add(
        "ge_density_feed", 18.45, "MJ/kg DM",
        distribution="normal", sd=0.5,
        reference=Reference("IPCC 2006, Vol.4 Ch.10 (p.10.22)"),
        description="Gross energy density of feed dry matter.",
    )
    ps.add(
        "n2o_n_to_n2o", 44.0 / 28.0, "kg N2O/kg N2O-N",
        reference=Reference("IPCC 2006, Vol.4 Ch.11, Eq. 11.1"),
        description="N2O-N -> N2O conversion (molar mass).",
    )
    ps.add(
        "c_to_co2", 44.0 / 12.0, "kg CO2/kg C",
        reference=Reference("IPCC 2006, Vol.4 Ch.2"),
        description="Carbon -> CO2 conversion (molar mass).",
    )

    # --- Enteric methane (Tier-2 and Tier-3) -------------------------
    ps.add(
        "cfi_growing_cattle", 0.322, "MJ/d/kg BW^0.75",
        distribution="normal", sd=0.012,
        reference=Reference("IPCC 2006, Vol.4 Ch.10, Table 10.4"),
        description="NEm coefficient for growing cattle (0.322).",
    )
    ps.add(
        "ca_activity_grazing", 0.17, "MJ/d/MJ NEm",
        distribution="triangular", min_=0.10, max_=0.36,
        reference=Reference("IPCC 2006, Vol.4 Ch.10, Table 10.5 (grazing)"),
        description="Activity coefficient at pasture.",
    )
    ps.add(
        "ym_grass_diet", 0.065, "fraction of GE",
        distribution="normal", sd=0.008,
        reference=Reference(
            "IPCC 2019 Refinement, Vol.4 Ch.10, Table 10.12 (diet >90 kg DM/1000 kg BW)"
        ),
        description="Ym (enteric methane, Tier-2) grass/concentrate diet.",
    )
    ps.add(
        "ym_feedlot", 0.030, "fraction of GE",
        distribution="normal", sd=0.003,
        reference=Reference("IPCC 2006, Vol.4 Ch.10, Table 10.12"),
        description="Ym for >90 % concentrate diets (finishing).",
    )
    # Tier-3: Mills et al. (2003) — exponential saturation equation
    # (via Ellis et al. 2009, equation W2): CH4 = 10.8 × (1 − e^(−0.141 × DMI))
    ps.add(
        "t3_mills_max_ch4", 10.8, "MJ/d",
        distribution="normal", sd=1.45,
        reference=Reference(
            "Ellis et al. 2009 (based on Mills et al. 2003), exponential saturation equation"
        ),
        description="Tier-3: asymptotic CH4 plateau (MJ/d) at high DMI.",
    )
    ps.add(
        "t3_mills_k", 0.141, "1/(kg DM/d)",
        distribution="normal", sd=0.0381,
        reference=Reference(
            "Ellis et al. 2009 (based on Mills et al. 2003), exponential saturation equation"
        ),
        description="Tier-3: saturation rate constant (per kg DM intake).",
    )
    ps.add(
        "cp_feed", 0.14, "kg N/kg DM intake",
        distribution="normal", sd=0.015,
        reference=Reference(
            "INRA/Efese coord., typical values for temperate beef cattle diets"
        ),
        description="Nitrogen intake per kg DM (N excretion, IPCC Eq. 10.32-10.33).",
    )

    # --- Manure management (CH4 manure + N2O manure) -------------------
    ps.add(
        "bo_cattle_manure", 0.13, "m3 CH4/kg VS",
        distribution="normal", sd=0.01,
        reference=Reference("IPCC 2019, Vol.4 Ch.10, Table 10.16 (Europe, cattle)"),
        description="Maximum methane producing potential of manure (B0).",
    )
    ps.add(
        "mcf_solid_storage", 0.02, "fraction",
        distribution="triangular", min_=0.01, max_=0.05,
        reference=Reference("IPCC 2006, Vol.4 Ch.10, Table 10.17 (solid storage)"),
        description="MCF solid storage, cool temperate climate.",
    )
    ps.add(
        "mcf_prp", 0.005, "fraction",
        distribution="triangular", min_=0.001, max_=0.02,
        reference=Reference(
            "IPCC 2019, Vol.4 Ch.10, Table 10.17 (paddock/pasture, field deposition)"
        ),
        description="MCF of manure deposited at pasture (permanent grassland).",
    )
    ps.add(
        "ue_fraction_ge", 0.04, "fraction of GE",
        reference=Reference("IPCC 2006, Vol.4 Ch.10, Eq. 10.24"),
        description="Urinary energy as a fraction of gross energy (ruminants).",
    )
    ps.add(
        "ash_fraction_manure", 0.08, "fraction of DM",
        reference=Reference("IPCC 2006, Vol.4 Ch.10, Eq. 10.24"),
        description="Ash content of manure (fraction of DM intake).",
    )
    ps.add(
        "ef3_solid_storage", 0.005, "kg N2O-N/kg N",
        distribution="normal", sd=0.003,
        reference=Reference("IPCC 2019, Vol.4 Ch.10, Table 10.21 (solid storage)"),
        description="EF3 direct N2O from solid manure storage.",
    )
    ps.add(
        "ef3_prp_cattle", 0.004, "kg N2O-N/kg N",
        distribution="normal", sd=0.0015,
        reference=Reference(
            "IPCC 2019, Vol.4 Ch.11, Table 11.1 (EF3PRP cattle, wet climate)"
        ),
        description="EF3 N2O from urine/dung deposits at pasture.",
    )
    ps.add(
        "frac_gasms_solid", 0.30, "fraction",
        distribution="normal", sd=0.05,
        reference=Reference("IPCC 2006, Vol.4 Ch.10, Table 10.22 (solid storage)"),
        description="NH3/NOx volatilisation during solid storage (FracGasMS).",
    )
    ps.add(
        "frac_leachms_solid", 0.30, "fraction",
        distribution="normal", sd=0.05,
        reference=Reference("IPCC 2006, Vol.4 Ch.10, Table 10.23 (solid storage)"),
        description="N leaching during solid storage (FracLeachMS).",
    )

    # --- Soil: direct and indirect N2O (2019 Refinement) ----------------
    ps.add(
        "ef1_soil", 0.01, "kg N2O-N/kg N",
        distribution="normal", sd=0.0012,
        reference=Reference(
            "IPCC 2019 Refinement, Vol.4 Ch.11, Table 11.1 (aggregated EF1)"
        ),
        description="EF1: direct N2O from N inputs to soil (synthetic + organic).",
    )
    ps.add(
        "ef4_deposition", 0.010, "kg N2O-N/kg N volatilised",
        distribution="normal", sd=0.0018,
        reference=Reference(
            "IPCC 2019 Refinement, Vol.4 Ch.11, Table 11.3 (aggregated EF4)"
        ),
        description="EF4: N2O from atmospheric re-deposition of volatilised N.",
    )
    ps.add(
        "ef5_leaching", 0.011, "kg N2O-N/kg N leached",
        distribution="normal", sd=0.0009,
        reference=Reference(
            "IPCC 2019 Refinement, Vol.4 Ch.11, Table 11.3 (aggregated EF5)"
        ),
        description="EF5: N2O from N leaching / runoff.",
    )
    ps.add(
        "frac_gasf", 0.11, "fraction",
        distribution="normal", sd=0.02,
        reference=Reference(
            "IPCC 2019 Refinement, Vol.4 Ch.11, Table 11.3 (aggregated FracGASF)"
        ),
        description="NH3/NOx volatilisation from synthetic fertilisers (FracGASF).",
    )
    ps.add(
        "frac_gasm", 0.21, "fraction",
        distribution="normal", sd=0.03,
        reference=Reference(
            "IPCC 2019 Refinement, Vol.4 Ch.11, Table 11.3 (aggregated FracGASM)"
        ),
        description="NH3/NOx volatilisation from organic fertilisers and field deposits (FracGASM).",
    )
    ps.add(
        "frac_leach", 0.24, "fraction",
        distribution="normal", sd=0.03,
        reference=Reference(
            "IPCC 2019 Refinement, Vol.4 Ch.11, Table 11.3 (aggregated FracLEACH-(H))"
        ),
        description="Fraction of N leached (FracLEACH, wet temperate climate).",
    )

    # --- Soil carbon ---------------------------------------------------
    ps.add(
        "soc_ref_temp_moist", 88.0, "tC/ha",
        distribution="normal", sd=15.0,
        reference=Reference("IPCC 2006, Vol.4 Ch.2, Table 2.3 (wet temperate climate, finely textured soils)"),
        description="Reference soil organic carbon stock.",
    )
    ps.add(
        "flu_grassland", 1.0, "-",
        reference=Reference("IPCC 2006, Vol.4 Ch.2, Table 2.5 (grasslands)"),
        description="Land-use factor (permanent grassland).",
    )
    ps.add(
        "flu_cropland", 0.82, "-",
        distribution="normal", sd=0.08,
        reference=Reference("IPCC 2006, Vol.4 Ch.2, Table 2.5 (croplands)"),
        description="Land-use factor (cropland).",
    )
    ps.add(
        "fmg_full_tillage", 1.0, "-",
        reference=Reference("IPCC 2006, Vol.4 Ch.2, Table 2.6"),
        description="Management factor (full tillage).",
    )
    ps.add(
        "fmg_reduced_tillage", 1.05, "-",
        distribution="normal", sd=0.03,
        reference=Reference("IPCC 2006, Vol.4 Ch.2, Table 2.6"),
        description="Management factor (reduced tillage).",
    )
    ps.add(
        "fi_high_input", 1.17, "-",
        distribution="normal", sd=0.05,
        reference=Reference("IPCC 2006, Vol.4 Ch.2, Table 2.7"),
        description="Input factor (high organic inputs).",
    )
    ps.add(
        "fi_medium_input", 1.0, "-",
        reference=Reference("IPCC 2006, Vol.4 Ch.2, Table 2.7"),
        description="Input factor (medium inputs).",
    )
    ps.add(
        "soc_eq_years", 20.0, "years",
        reference=Reference("IPCC 2006, Vol.4 Ch.2, Eq. 2.25 (default D)"),
        description="Equilibrium time-lapse between two reference stocks (D).",
    )

    # --- Indirect emissions: purchases ----------------------------------
    ps.add(
        "ef_purchase_calf", 2.5, "kg CO2e/kg liveweight purchased",
        distribution="lognormal", sd=0.25,
        reference=Reference(
            "Agribalyse 3.0 (ecological basis) — sucker herd, male calf"
        ),
        description="Upstream footprint of calf purchase (sucker herd).",
    )
    ps.add(
        "ef_concentrate_feed", 0.55, "kg CO2e/kg DM",
        distribution="lognormal", sd=0.20,
        reference=Reference("Agribalyse 3.0.1 (feed materials)"),
        description="Upstream footprint of purchased concentrates (cereals, oilseed meals).",
    )
    ps.add(
        "ef_copproduct_feed", 0.25, "kg CO2e/kg DM",
        distribution="lognormal", sd=0.25,
        reference=Reference(
            "Ecoinvent/Agribalyse — crop co-products (bran, pulps); allocation matrices"
        ),
        description="Upstream footprint of purchased crop co-products.",
    )
    ps.add(
        "ef_synthetic_n", 4.5, "kg CO2e/kg N",
        distribution="lognormal", sd=0.15,
        reference=Reference(
            "Ecoinvent 3 / Agribalyse — production and transport of N fertiliser"
        ),
        description="Upstream footprint of synthetic nitrogen fertiliser production.",
    )
    ps.add(
        "ef_lime", 0.10, "kg CO2e/kg",
        distribution="normal", sd=0.02,
        reference=Reference("IPCC 2006, Vol.4 Ch.11, Eq. 11.13"),
        description="CO2 emission from agricultural lime application.",
    )
    ps.add(
        "ef_p2o5_fertilizer", 1.2, "kg CO2e/kg P2O5",
        distribution="lognormal", sd=0.20,
        reference=Reference("Ecoinvent 3 / Agribalyse — phosphate fertilisers"),
        description="Upstream footprint of phosphate fertilisers.",
    )
    ps.add(
        "ef_k2o_fertilizer", 0.55, "kg CO2e/kg K2O",
        distribution="lognormal", sd=0.20,
        reference=Reference("Ecoinvent 3 / Agribalyse — potash fertilisers"),
        description="Upstream footprint of potash fertilisers.",
    )
    ps.add(
        "ef_seed", 0.9, "kg CO2e/kg seed",
        distribution="lognormal", sd=0.25,
        reference=Reference("Agribalyse 3.0.1 — seeds"),
        description="Upstream footprint of seeds.",
    )
    ps.add(
        "ef_p2o5_rock", 0.2, "kg CO2e/kg P2O5",
        distribution="lognormal", sd=0.20,
        reference=Reference("Ecoinvent 3 — rock phosphates"),
        description="Upstream footprint of phosphate amendments (slag).",
    )

    # --- Mechanical fieldwork ------------------------------------------
    ps.add(
        "ef_fuel_diesel", 2.66, "kg CO2e/L diesel",
        reference=Reference("ADEME Base Carbone (off-road diesel, upstream + combustion)"),
        description="Emission factor of tractor diesel.",
    )
    ps.add(
        "fuel_ploughing", 55.0, "L/ha",
        distribution="normal", sd=10.0,
        reference=Reference("Mechanised fieldwork literature (deep tillage)"),
        description="Diesel consumed for ploughing.",
    )
    ps.add(
        "fuel_harvest", 35.0, "L/ha",
        distribution="normal", sd=7.0,
        reference=Reference("Mechanised fieldwork literature (harvest)"),
        description="Diesel consumed for harvesting (silage/threshing).",
    )
    ps.add(
        "fuel_spray", 12.0, "L/ha",
        distribution="normal", sd=3.0,
        reference=Reference("Mechanised fieldwork literature (spraying)"),
        description="Diesel consumed for one sprayer pass.",
    )
    ps.add(
        "fuel_spreading", 15.0, "L/ha",
        distribution="normal", sd=3.0,
        reference=Reference("Mechanised fieldwork literature (spreading)"),
        description="Diesel consumed for spreading (manure/mineral).",
    )
    ps.add(
        "fuel_seed_op", 15.0, "L/ha",
        distribution="normal", sd=3.0,
        reference=Reference("Mechanised fieldwork literature (sowing)"),
        description="Diesel consumed for sowing.",
    )
    ps.add(
        "fuel_mowing", 18.0, "L/ha",
        distribution="normal", sd=4.0,
        reference=Reference("Mechanised fieldwork literature (mowing)"),
        description="Diesel consumed for grass mowing.",
    )
    ps.add(
        "fuel_tillage_reduced", 20.0, "L/ha",
        distribution="normal", sd=4.0,
        reference=Reference("Mechanised fieldwork literature (shallow tillage)"),
        description="Diesel consumed for shallow tillage.",
    )

    return ps
