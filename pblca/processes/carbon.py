"""Layer 1 — Soil carbon stock change (CO2 sink/emission).

``soil_carbon`` slot: annual change in soil organic carbon stock
according to the IPCC 2006 method (Eq. 2.25, Vol.4 Ch.2) — stock
difference method with FLU/FMG/FI factors and equilibrium duration
D = 20 years.

Two variants:
  * ``ipcc_stock_change``: IPCC factor-based approach,
  * ``rothc_like``: simplified Tier-3 variant, RothC/AMG-like, where the
    annual change is parameterised by a sequestration rate (tC/ha/yr)
    per land use — demonstrating the registry's extensibility (real
    AMG/RothC models pluggable through the same interface).
"""

from __future__ import annotations

from ..registry import ModelContext, ModelResult, ModelSpec

REF_IPCC = "IPCC 2006, Vol.4 Ch.2, Eq. 2.25 (Table 2.3/2.5/2.6/2.7)"
REF_T3 = "RothC/AMG-like model (per-use sequestration rate, parameterisable)"


def soil_carbon_ipcc(ctx: ModelContext) -> ModelResult:
    """Soil C stock change from IPCC factors (Eq. 2.25).

    ΔC = (SOC0 − SOC(0−T)) / D, SOC = SOC_REF × FLU × FMG × FI, D = 20 yr.
    A stock at equilibrium under permanent grassland (FLU=1) gives
    ΔC = 0; cropland↔temporary grassland conversions generate the flux.

    Returns:
        ModelResult with co2_kg = ΔC × 44/12 × (−1): a stock loss is an
        emission (positive CO2), a gain a sink (negative).
    """
    v = ctx.v
    res = ModelResult(model_name="ipcc_stock_change")
    d_years = v("soc_eq_years")
    total_c_t = 0.0
    res.trace["per_parcel"] = {}
    for p in ctx.farm.parcels:
        soc_current = p.soc_ref * p.flu * p.fmg * p.fi
        if p.soc_initial is None:
            # Established management: equilibrium reached, ΔC = 0
            # (Eq. 2.25 with SOC0 = SOC(0-T)). No flux inventoried.
            delta_c = 0.0
            state = "equilibrium (established management)"
        else:
            # Ongoing transition: SOC(0-T) -> SOC0 over D years.
            delta_c = (p.soc_initial - soc_current) * p.area / d_years
            state = "transition"
        # delta_c > 0: the soil is rebuilding (sink); < 0: emission.
        total_c_t += delta_c
        res.trace["per_parcel"][p.key] = {
            "soc_current_t_ha": soc_current,
            "delta_c_t_per_year": delta_c,
            "state": state,
        }
    # CO2 conversion: sink => negative CO2 (storage), emission => positive.
    res.co2_kg = -total_c_t * v("c_to_co2") * 1000.0
    res.fluxes["delta_c_t_per_year"] = total_c_t
    return res


def soil_carbon_rothc_like(ctx: ModelContext) -> ModelResult:
    """Tier-3 variant (RothC/AMG-like) of soil carbon.

    Uses net sequestration rates per land use (parameterisable in the
    ParameterSet: ``seq_rate_<land use>``). If missing for a parcel,
    falls back to the IPCC variant (graceful degradation).

    Returns:
        ModelResult with co2_kg (negative = sink).
    """
    v = ctx.v
    res = ModelResult(model_name="rothc_like")
    total_c_t = 0.0
    res.trace["per_parcel"] = {}
    for p in ctx.farm.parcels:
        pid = f"seq_rate_{p.crop}"
        if pid in ctx.values:
            rate = ctx.values[pid]  # tC/ha/yr (negative = storage)
            delta_c = rate * p.area
            src = "dedicated parameter"
        else:
            ctx.logger.warn(
                "soil_carbon_t3",
                f"Sequestration rate missing for {p.crop}; IPCC fallback",
            )
            # Fallback: IPCC factors (identical to variant 1)
            soc_current = p.soc_ref * p.flu * p.fmg * p.fi
            soc_before = p.soc_ref * v("flu_grassland") * 1.0 * 1.0
            delta_c = (soc_before - soc_current) * p.area / v("soc_eq_years")
            src = "IPCC fallback"
        total_c_t += delta_c
        res.trace["per_parcel"][p.key] = {
            "delta_c_t_per_year": delta_c,
            "source": src,
        }
    res.co2_kg = -total_c_t * v("c_to_co2") * 1000.0
    res.fluxes["delta_c_t_per_year"] = total_c_t
    return res


SPECS = [
    ModelSpec(
        slot="soil_carbon",
        variant="ipcc_stock_change",
        tier="Tier-1/2",
        func=soil_carbon_ipcc,
        reference=REF_IPCC,
        description="Soil ΔC from FLU/FMG/FI factors (Eq. 2.25, D=20 yr).",
    ),
    ModelSpec(
        slot="soil_carbon",
        variant="rothc_like",
        tier="Tier-3",
        func=soil_carbon_rothc_like,
        reference=REF_T3,
        description="Per-use sequestration rate (pluggable AMG/RothC).",
    ),
]
