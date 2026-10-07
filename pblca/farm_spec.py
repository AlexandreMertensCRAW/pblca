"""Declarative farm specification: values only, one builder for all farms.

A :class:`FarmSpec` describes a farm as data (animal groups, parcels,
purchases, manure management, climate); :func:`build_farm` is the single
generic builder that translates any spec into a :class:`FarmContext`,
owning all the shared logic:

* the average annual headcount of each group is DECLARED on the
  group (``n_head``) — in a herd with cows, calves are born on the
  farm, so the headcounts are decoupled from the purchases;
* parcel carbon/fuel factors: ``deep_tillage`` selects the traceable
  parameter set (full vs reduced tillage, cropland vs grassland land-use
  and management factors) instead of hard-coded values;
* lime declared in t/ha, converted to kg/ha;
* purchased young animals are declared in ``purchases`` as a count
  and a live weight (``n_calves_purchased`` x ``calf_purchased_bw_kg``);
  ``build_farm`` derives the ``n_calves_purchased_kg_lw`` flow consumed
  by the purchases model.

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

    Fattening fields (bw_start/bw_end/days, share_concentrate) and
    dairy/livestock fields (milk_prot, milk_fat, pregnant,
    work_hours) are all first-class citizens: the spec describes any
    production system. The time at pasture is NOT declared here: it
    is derived from the grazing events of the parcels
    (:class:`GrazingEventSpec`) — one source of truth. On-farm measurements (dmi_measured, ge_measured,
    ration_rel_sd, ch4_measured_ahcs, diet_om/diet_omd) are declared
    here, on the group they belong to. Fields left to None keep the
    registry defaults. ``extra`` is an escape hatch for any other
    ``AnimalGroup`` attribute (case-specific inputs).

    ``n_head`` (average annual headcount) is DECLARED here: the
    herd dynamics (births, purchases, sales) belongs to the farm
    data, not to the builder.
    """

    key: str
    n_head: float
    days: int
    bw_start: float
    bw_end: float
    diet_de: float
    share_concentrate: float = 0.0
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
class GrazingEventSpec:
    """One grazing event: an animal group on a parcel, between two
    dates. The duration (days) is derived from the dates; several
    events of the same (group, parcel) are summed.

    Attributes:
        group: animal group key (must exist in the farm animals).
        entry: entry date (YYYY-MM-DD).
        exit: exit date (YYYY-MM-DD), after the entry.
    """

    group: str
    entry: str
    exit: str


@dataclass
class OrganicFertilisationSpec:
    """One dated organic fertilisation event on a parcel (solid
    manure, compost, ...): a vector, several events per year.

    Attributes:
        type: fertiliser type ("solid_manure", "compost", ...); kept
            for traceability (future per-form NH3 differentiation).
        date: application date (YYYY-MM-DD); stored for the future
            time-dynamic soil model, not consumed by the annual one.
        n_kg: organic nitrogen of THIS application on the parcel
            (kg N); the builder sums the events into kg N/ha/yr.
    """

    type: str
    date: str
    n_kg: float


@dataclass
class SyntheticFertilisationSpec:
    """One dated synthetic fertilisation event on a parcel: a
    vector, several applications per year.

    Attributes:
        type: fertiliser form ("ammonitrate", "urea", ...); kept for
            traceability (future per-form emission factors).
        date: application date (YYYY-MM-DD); stored for the future
            time-dynamic soil model, not consumed by the annual one.
        n_kg: synthetic nitrogen of THIS application on the parcel
            (kg N); the builder sums the events into kg N/ha/yr.
    """

    type: str = "mineral_n"
    date: str = ""
    n_kg: float = 0.0


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
    lime_t_ha: float = 0.0
    deep_tillage: bool = False
    is_grassland: Optional[bool] = None
    n_residue: float = 0.0
    fuel_pids: Optional[List[str]] = None
    flu_pid: Optional[str] = None
    fmg_pid: Optional[str] = None
    fi_pid: Optional[str] = None
    grazing: Optional[List[GrazingEventSpec]] = None
    organic_fertilisation: Optional[List[OrganicFertilisationSpec]] = None
    synthetic_fertilisation: Optional[List[SyntheticFertilisationSpec]] = None


@dataclass
class FarmSpec:
    """Declarative description of a farm: values only, no logic.

    Attributes:
        farm_id: farm identifier (used in the gas ledger traceability).
        animals: animal groups (age classes / herds), each with its
            DECLARED average annual headcount (``n_head``) — decoupled
            from the purchases (a herd with cows births its calves).
        parcels: land parcels (grasslands and rotation crops).
        purchases: annual purchased inputs (keys as used by the
            purchases model, e.g. "concentrate_kg_dm").
        (calf purchases are declared in ``purchases`` as
        ``n_calves_purchased`` and ``calf_purchased_bw_kg``.)
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
        n_synthetic=0.0,
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

    # ---- Grazing events: validation, per-group days at pasture and
    # ---- per-parcel routing of the deposited nitrogen.
    from datetime import date as _date

    def _valid_date(text, context):
        try:
            return _date.fromisoformat(text)
        except (TypeError, ValueError):
            raise ValueError(
                f"{context}: invalid date '{text}' (expected YYYY-MM-DD)"
            ) from None

    def _event_days(ev, parcel_key):
        d0 = _valid_date(ev.entry, f"grazing of '{ev.group}' on '{parcel_key}'")
        d1 = _valid_date(ev.exit, f"grazing of '{ev.group}' on '{parcel_key}'")
        if d1 <= d0:
            raise ValueError(
                f"grazing of '{ev.group}' on '{parcel_key}': exit "
                f"({ev.exit}) must be after the entry ({ev.entry})"
            )
        return (d1 - d0).days

    group_keys = {a.key for a in spec.animals}
    grazing_days = {k: 0.0 for k in group_keys}
    parcel_grazing_days: Dict[str, Dict[str, float]] = {}
    for p in spec.parcels:
        per_group: Dict[str, float] = {}
        for ev in p.grazing or []:
            if ev.group not in group_keys:
                raise ValueError(
                    f"grazing on parcel '{p.key}': unknown animal group "
                    f"'{ev.group}' (groups: {sorted(group_keys)})"
                )
            days = _event_days(ev, p.key)
            grazing_days[ev.group] += days
            per_group[ev.group] = per_group.get(ev.group, 0.0) + days
        if per_group:
            parcel_grazing_days[p.key] = per_group
    for key, days in grazing_days.items():
        if days > 365:
            raise ValueError(
                f"grazing events of group '{key}': {days} days at "
                "pasture exceed one year"
            )

    animals = []
    for a in spec.animals:
        group_kwargs: Dict[str, Any] = {
            "key": a.key,
            "n_head": a.n_head,
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
            "grazing": min(grazing_days[a.key] / 365.0, 1.0),
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
    # ---- Dated fertilisation events -> per-parcel annual rates -------
    # Dates are validated for traceability (future dynamic soil model);
    # the annual model sums the events per parcel.
    for p, ctx_p in zip(spec.parcels, parcels):
        for f in p.synthetic_fertilisation or []:
            if f.n_kg < 0:
                raise ValueError(
                    f"synthetic fertilisation of parcel '{p.key}': "
                    "'n_kg' must be positive"
                )
            if f.date:
                _valid_date(
                    f.date, f"synthetic fertilisation of parcel '{p.key}'"
                )
            ctx_p.n_synthetic += f.n_kg / ctx_p.area
        for f in p.organic_fertilisation or []:
            if f.n_kg < 0:
                raise ValueError(
                    f"organic fertilisation of parcel '{p.key}': "
                    "'n_kg' must be positive"
                )
            if f.date:
                _valid_date(
                    f.date, f"organic fertilisation of parcel '{p.key}'"
                )
            ctx_p.n_organic_spread += f.n_kg / ctx_p.area

    purchases = dict(spec.purchases)
    n_calves = purchases.pop("n_calves_purchased", 0.0)
    calf_bw = purchases.pop("calf_purchased_bw_kg", 0.0)
    if n_calves < 0 or calf_bw < 0:
        raise ValueError(
            "purchases: 'n_calves_purchased' and 'calf_purchased_bw_kg' "
            "must be positive"
        )
    if n_calves > 0 and "n_calves_purchased_kg_lw" not in purchases:
        purchases["n_calves_purchased_kg_lw"] = n_calves * calf_bw

    return FarmContext(
        farm_id=spec.farm_id,
        animals=animals,
        parcels=parcels,
        purchases=purchases,
        manure_split=dict(spec.manure_split),
        manure_exported=spec.manure_exported,
        avg_temp=spec.avg_temp,
        mature_weight=spec.mature_weight,
        parcel_grazing_days=parcel_grazing_days or None,
    )
