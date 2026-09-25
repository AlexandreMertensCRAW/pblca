"""Layer 1 — Mechanical fieldwork (diesel of field operations).

``fieldwork`` slot: diesel consumption per field operation
(ploughing, sowing, spreading, spraying, mowing, harvest, shallow
tillage), converted to CO2 via the diesel emission factor
(upstream + combustion, ADEME Base Carbone).

The per-operation detail is declared per parcel in
``LandParcel.fuel_use`` (L/ha/yr), or reconstructed from a cropping
plan; both inputs are traceable.
"""

from __future__ import annotations

from ..registry import ModelContext, ModelResult, ModelSpec

REF = "ADEME Base Carbone — off-road diesel (upstream + combustion)"


def fieldwork_fuel(ctx: ModelContext) -> ModelResult:
    """CO2 from the farm's mechanical fieldwork.

    Returns:
        ModelResult with the total co2_kg and a trace per parcel.
    """
    v = ctx.v
    res = ModelResult(model_name="fuel_ademe")
    total = 0.0
    res.trace["per_parcel"] = {}
    for p in ctx.farm.parcels:
        if p.area <= 0:
            continue
        litres = p.fuel_use * p.area
        co2 = litres * v("ef_fuel_diesel")
        res.trace["per_parcel"][p.key] = {
            "fuel_l": litres,
            "co2_kg": co2,
        }
        total += co2
    res.co2_kg = total
    res.fluxes["fuel_l"] = sum(
        p.fuel_use * p.area for p in ctx.farm.parcels if p.area > 0
    )
    return res


SPECS = [
    ModelSpec(
        slot="fieldwork",
        variant="fuel_ademe",
        tier="Tier-1 (energy factor)",
        func=fieldwork_fuel,
        reference=REF,
        description="Field-operation diesel × ADEME emission factor.",
    ),
]
