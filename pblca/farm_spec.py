"""Declarative farm specification: values only, one builder for all farms.

A :class:`FarmSpec` describes a farm as data (animal groups, parcels,
purchases, manure management, climate); :func:`build_farm` is the single
generic builder that translates any spec into a :class:`FarmContext`,
owning all the shared logic:

* steady-state average annual headcount of each age class:
  ``n_head = n_purchased * days / 365`` (purchased young stock going
  through the classes in overlapping batches);
* parcel carbon/fuel factors: ``deep_tillage`` selects the traceable
  parameter set (full vs reduced tillage, cropland vs grassland land-use
  and management factors) instead of hard-coded values;
* lime declared in t/ha, converted to kg/ha;
* calf purchases derived from the first age class
  (``n_purchased * bw_start``).

Adding a new case study = writing one :class:`FarmSpec`; no logic is
duplicated. On-farm measurements (ration sheets, GreenFeed monitoring,
INRA diet characterisation) are declared per group in
:class:`AnimalGroupSpec` — they are farm data, so they belong to the
farm description. The legacy ``GroupMeasurements`` layer
(``pblca.scenarios``) remains supported for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .params import ParameterSet, build_default_parameter_set
from .registry import AnimalGroup, FarmContext, LandParcel


@dataclass
class AnimalGroupSpec:
    """One animal group (age class or herd), declared with values only.

    Fattening fields (bw_start/bw_end/days, share_concentrate, grazing)
    and dairy/livestock fields (milk_prot, milk_fat, pregnant,
    work_hours) are all first-class citizens: the spec describes any
    production system. On-farm measurements (dmi_measured, ge_measured,
    ration_rel_sd, ch4_measured_ahcs, diet_om/diet_omd) are declared
    here, on the group they belong to. Fields left to None keep the
    registry defaults. ``extra`` is an escape hatch for any other
    ``AnimalGroup`` attribute (case-specific inputs).

    ``n_head`` is NOT declared: it is computed by :func:`build_farm`
    from ``n_purchased`` and ``days`` (steady-state headcount).
    """

    key: str
    days: int
    bw_start: float
    bw_end: float
    diet_de: float
    share_concentrate: float = 0.0
    grazing: float = 1.0
    system: Optional[str] = None
    diet_ge_density: Optional[float] = None
    milk_prot: float = 0.0
    milk_fat: float = 0.0
    work_hours: float = 0.0
    pregnant: bool = False
    # On-farm measurements (ration mode "measured" when set).
    dmi_measured: Optional[float] = None
    ge_measured: Optional[float] = None
    ration_rel_sd: Optional[float] = None
    ch4_measured_ahcs: Optional[float] = None
    ch4_measured_ahcs_rel_sd: Optional[float] = None
    diet_om: Optional[float] = None
    diet_omd: Optional[float] = None
    extra: Optional[Dict[str, Any]] = None


@dataclass
class ParcelSpec:
    """One land parcel, declared with values only.

    ``deep_tillage`` selects the traceable carbon/fuel parameter set
    of :func:`build_farm` (full tillage: cropland land-use factor,
    full-tillage management factor, ploughing fuel; reduced tillage:
    grassland factor when the crop is a grassland, reduced-tillage
    management factor, reduced-tillage fuel). ``is_grassland`` is
    inferred from the crop name when left to None. ``lime_t_ha`` is
    declared in t/ha/yr and converted to kg/ha/yr.

    Parameter overrides (all optional): ``fuel_pids`` lists the fuel
    parameter ids summed into ``fuel_use`` (default: derived from
    ``deep_tillage``), and ``flu_pid`` / ``fmg_pid`` / ``fi_pid`` name
    the land-use / management / input factor parameters (defaults:
    derived from ``deep_tillage`` and ``is_grassland``). These give
    exact control for parcels outside the two standard situations —
    e.g. a mown permanent grassland: ``fuel_pids=["fuel_mowing"],
    fmg_pid="fmg_full_tillage", fi_pid="fi_medium_input"``.
    """

    key: str
    crop: str
    area: float
    n_synthetic: float = 0.0
    lime_t_ha: float = 0.0
    deep_tillage: bool = False
    is_grassland: Optional[bool] = None
    n_residue: float = 0.0
    fuel_pids: Optional[List[str]] = None
    flu_pid: Optional[str] = None
    fmg_pid: Optional[str] = None
    fi_pid: Optional[str] = None


@dataclass
class FarmSpec:
    """Declarative description of a farm: values only, no logic.

    Attributes:
        farm_id: farm identifier (used in the gas ledger traceability).
        animals: animal groups (age classes / herds). ``n_head`` of
            each group is computed from ``n_purchased``.
        parcels: land parcels (grasslands and rotation crops).
        purchases: annual purchased inputs (keys as used by the
            purchases model, e.g. "concentrate_kg_dm").
        n_purchased: young animals purchased per year (drives the
            steady-state headcounts and the calf purchase flow).
        manure_split: share of excretions per management system
            (e.g. {"pasture": 0.45, "solid_storage": 0.55}).
        manure_exported: share of stored manure exported off-farm.
        avg_temp: mean annual temperature (°C) — MCF of manure systems.
        mature_weight: mature liveweight of the animals (kg, IPCC
            Eq. 10.6 MW); breed-dependent.
    """

    farm_id: str
    animals: List[AnimalGroupSpec]
    parcels: List[ParcelSpec]
    purchases: Dict[str, float]
    n_purchased: float
    manure_split: Dict[str, float]
    manure_exported: float = 0.0
    avg_temp: float = 10.0
    mature_weight: float = 700.0


def _parcel(spec: ParcelSpec, values: Dict[str, float]) -> LandParcel:
    """Translate one ParcelSpec into a LandParcel.

    The carbon and fuel factors come from the traceable parameter set
    (Monte-Carlo propagated): the spec only declares the management
    choice (deep vs reduced tillage), never the factor values.
    """
    is_grassland = spec.is_grassland
    if is_grassland is None:
        is_grassland = "prairie" in spec.crop
    if spec.fuel_pids is not None:
        fuel_use = sum(values[pid] for pid in spec.fuel_pids)
    elif spec.deep_tillage:
        fuel_use = (
            values["fuel_ploughing"] + values["fuel_seed_op"]
            + values["fuel_harvest"]
        )
    else:
        fuel_use = (
            values["fuel_tillage_reduced"] + values["fuel_seed_op"]
            + values["fuel_harvest"]
        )
    if spec.flu_pid is not None:
        flu = values[spec.flu_pid]
    elif spec.deep_tillage:
        flu = values["flu_cropland"]
    else:
        flu = values["flu_grassland"] if is_grassland else values["flu_cropland"]
    if spec.fmg_pid is not None:
        fmg = values[spec.fmg_pid]
    elif spec.deep_tillage:
        fmg = values["fmg_full_tillage"]
    else:
        fmg = values["fmg_reduced_tillage"]
    fi = values[spec.fi_pid] if spec.fi_pid is not None else values["fi_high_input"]
    return LandParcel(
        key=spec.key,
        crop=spec.crop,
        area=spec.area,
        n_synthetic=spec.n_synthetic,
        lime=spec.lime_t_ha * 1000.0,
        n_residue=spec.n_residue,
        fuel_use=fuel_use,
        soc_ref=values["soc_ref_temp_moist"],
        flu=flu,
        fmg=fmg,
        fi=fi,
        is_grassland=is_grassland,
    )


def build_farm(
    spec: FarmSpec, params: Optional[ParameterSet] = None
) -> FarmContext:
    """Build a FarmContext from a declarative FarmSpec (generic builder).

    Args:
        spec: the farm description (values only).
        params: parameter set (default: the standard traceable set);
            its central values provide the SOC/fuel factors.

    Returns:
        a complete FarmContext ready for simulation.
    """
    values = (params or build_default_parameter_set()).central_values()

    animals = []
    for a in spec.animals:
        group_kwargs: Dict[str, Any] = {
            "key": a.key,
            "n_head": spec.n_purchased * a.days / 365.0,
            "bw_start": a.bw_start,
            "bw_end": a.bw_end,
            "days": a.days,
            "diet_de": a.diet_de,
            "diet_ge_density": a.diet_ge_density,
            "share_concentrate": a.share_concentrate,
            "milk_prot": a.milk_prot,
            "milk_fat": a.milk_fat,
            "work_hours": a.work_hours,
            "pregnant": a.pregnant,
            "grazing": a.grazing,
            "dmi_measured": a.dmi_measured,
            "ge_measured": a.ge_measured,
            "ration_rel_sd": a.ration_rel_sd,
            "ch4_measured_ahcs": a.ch4_measured_ahcs,
            "ch4_measured_ahcs_rel_sd": a.ch4_measured_ahcs_rel_sd,
            "diet_om": a.diet_om,
            "diet_omd": a.diet_omd,
        }
        if a.system is not None:
            group_kwargs["system"] = a.system
        if a.extra:
            for attr, value in a.extra.items():
                group_kwargs[attr] = value
        animals.append(AnimalGroup(**group_kwargs))

    parcels = [_parcel(p, values) for p in spec.parcels]

    purchases = dict(spec.purchases)
    if spec.animals and "n_calves_purchased_kg_lw" not in purchases:
        first = spec.animals[0]
        purchases["n_calves_purchased_kg_lw"] = spec.n_purchased * first.bw_start

    return FarmContext(
        farm_id=spec.farm_id,
        animals=animals,
        parcels=parcels,
        purchases=purchases,
        manure_split=dict(spec.manure_split),
        manure_exported=spec.manure_exported,
        avg_temp=spec.avg_temp,
        mature_weight=spec.mature_weight,
    )
