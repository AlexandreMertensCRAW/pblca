"""Transversal layer: model registry and universal interface.

Requirement: "the model's modularity makes it possible to test alternative
equations (e.g. Tier-3) ... a model registry, so that each model can be
tested for every simulation. A universal interface for the models."

Each modelled phenomenon (enteric methane, manure CH4, manure N2O,
soil N2O, soil carbon, ...) has a ``slot`` key. For each slot, several
*variants* (Tier-2, Tier-3, ...) can be registered in the registry. A
simulation chooses, for each slot, the variant to use — which makes every
model testable at every simulation (explicit testability requirement).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .params import ParameterSet

# ----------------------------------------------------------------------
# Universal model interface
# ----------------------------------------------------------------------
# Common signature imposed on EVERY model in the registry:
#
#   def model(ctx: ModelContext) -> ModelResult
#
# ``ctx`` carries the required inputs (animals, parcels, drawn parameter
# values, message log); the model returns elementary fluxes (kg N, kg VS,
# ...) and/or gas emissions, NEVER impacts directly — gases are
# inventoried by layer 2. This single interface makes Tier-2 and
# Tier-3 interchangeable.


@dataclass
class AnimalGroup:
    """Homogeneous animal category (age class).

    Attributes:
        key: identifier (e.g. "0-6mo").
        n_head: average annual headcount (heads).
        bw_start: live weight at start of period (kg).
        bw_end: live weight at end of period (kg).
        days: duration of the period over the year (days).
        diet_de: energy digestibility of the diet (fraction, e.g. 0.65).
        diet_ge_density: gross energy density (MJ/kg DM).
        share_concentrate: share of concentrates in the diet (DM fraction).
        milk_prot: milk protein kg/d (0 for fattening).
        milk_fat: milk fat kg/d (0 for fattening).
        work_hours: working hours / day (0 here).
        pregnant: gestation (False here).
        grazing: fraction of the year at pasture (0-1).
        dmi_measured: on-farm measured dry matter intake (kg DM/head/d).
            When set (positive), it bypasses the IPCC energy-chain
            estimate for DMI (ration encoded "by hand" from farm
            measurements).
        ge_measured: on-farm measured gross energy intake (MJ/head/d).
            When set (positive), it bypasses the IPCC energy-chain
            estimate for GE.
        system: feeding situation per IPCC 2019 Refinement Vol.4
            Ch.10 (Table 10.5): "grazing" (>90 % of DM from grazing),
            "mixed" (grazing + conserved forages/concentrates) or
            "feedlot" (confined, high-concentrate). Used by the
            ``tier2_2019`` variant to pick the tabulated Ym of Table
            10.12 (Updated) without interpolation. None = inferred
            from the concentrate share (see ``_ym_2019``).
        ration_rel_sd: relative standard deviation of the measured-
            ration quantification error (e.g. 0.05 for ±5 %). During
            Monte-Carlo, one multiplicative lognormal factor per group
            and per iteration (median 1) is drawn and applied to BOTH
            ``dmi_measured`` and ``ge_measured``: the quantification
            error of the ration scales intake and gross energy together
            (the ratio GE = DMI × diet_ge_density is preserved, hence
            no spurious coherence warnings). The central run uses the
            unperturbed measured values.

    Two ration-definition modes are therefore available per group:

        1. ``ipcc_equations`` (default): GE and DMI are derived from net
           energy requirements and diet digestibility (IPCC 2006
           Vol.4 Ch.10, Eq. 10.3-10.16).
        2. ``measured``: GE and/or DMI are encoded directly from farm
           measurements (e.g. ration sheets, weighing, feed analysis).

    The mode applies wherever the group's intake is used (enteric CH4,
    manure CH4/N2O) so that enteric ↔ manure consistency is preserved.
    """

    key: str
    n_head: float
    bw_start: float
    bw_end: float
    days: float
    diet_de: float
    diet_ge_density: float
    share_concentrate: float = 0.0
    milk_prot: float = 0.0
    milk_fat: float = 0.0
    work_hours: float = 0.0
    pregnant: bool = False
    grazing: float = 1.0
    dmi_measured: Optional[float] = None
    ge_measured: Optional[float] = None
    ration_rel_sd: Optional[float] = None
    system: Optional[str] = None

    @property
    def ration_mode(self) -> str:
        """"ipcc_equations" when both measures are absent, "measured"
        when at least one is present."""
        if self.dmi_measured is not None or self.ge_measured is not None:
            return "measured"
        return "ipcc_equations"


@dataclass
class LandParcel:
    """Cropland or grassland parcel.

    Attributes:
        key: identifier.
        crop: crop name ("prairie_permanente", "pomme_de_terre", ...).
        area: area (ha).
        n_synthetic: synthetic nitrogen applied (kg N/ha/yr).
        n_organic_spread: organic nitrogen spread (kg N/ha/yr).
        n_residue: nitrogen of residues left in the field (kg N/ha/yr).
        n_excreta_grazing: nitrogen excreted at pasture (kg N/ha/yr).
        lime: lime applied (kg/ha/yr).
        fuel_use: diesel for mechanical operations (L/ha/yr).
        soc_ref: reference soil carbon stock (tC/ha).
        flu: land-use factor.
        fmg: management factor.
        fi: input factor.
        soc_initial: soil C stock at the start of the inventory period
            (tC/ha); None = equilibrium (ΔC = 0, established management).
        is_grassland: True for grassland (no tillage).
    """

    key: str
    crop: str
    area: float
    n_synthetic: float = 0.0
    n_organic_spread: float = 0.0
    n_residue: float = 0.0
    n_excreta_grazing: float = 0.0
    lime: float = 0.0
    fuel_use: float = 0.0
    soc_ref: float = 88.0
    flu: float = 1.0
    fmg: float = 1.0
    fi: float = 1.0
    soc_initial: Optional[float] = None
    is_grassland: bool = False


@dataclass
class FarmContext:
    """Complete context of a simulated farm (layer 1).

    Attributes:
        farm_id: farm identifier.
        animals: animal groups (age classes).
        parcels: land parcels.
        purchases: annual purchased inputs.
        manure_split: share of manure per management system
            (e.g. {"solid_storage": 0.55, "pasture": 0.45}).
        manure_exported: share of stored manure exported off-farm.
        avg_temp: mean annual temperature (°C) — MCF.
    """

    farm_id: str
    animals: List[AnimalGroup]
    parcels: List[LandParcel]
    purchases: Dict[str, float]
    manure_split: Dict[str, float]
    manure_exported: float = 0.0
    avg_temp: float = 10.0


@dataclass
class ModelContext:
    """Inputs passed to every model through the universal interface.

    Attributes:
        farm: the farm context (animals, parcels, inputs).
        params: the parameter set.
        values: pid -> value dictionary (central or drawn in Monte-Carlo).
        logger: warnings/errors log of the current simulation.
    """

    farm: FarmContext
    params: ParameterSet
    values: Dict[str, float]
    logger: "DiagLogger"

    def v(self, pid: str) -> float:
        """Current parameter value (central or drawn), traceable."""
        if pid not in self.values:
            raise KeyError(f"Parameter missing from the value set: {pid}")
        return self.values[pid]


class DiagLogger:
    """Log of warnings and errors of a simulation.

    ISO 14044 (section 4.5, critical review): diagnostics are part of
    the study documentation; they are stored with the simulation result.
    """

    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    def add(self, level: str, source: str, message: str) -> None:
        self.messages.append(
            {"level": level, "source": source, "message": message}
        )

    def warn(self, source: str, message: str) -> None:
        self.add("WARNING", source, message)

    def error(self, source: str, message: str) -> None:
        self.add("ERROR", source, message)

    @property
    def n_errors(self) -> int:
        return sum(1 for m in self.messages if m["level"] == "ERROR")

    def as_list(self) -> List[Dict[str, Any]]:
        return list(self.messages)


@dataclass
class ModelResult:
    """Standard output of every model (universal interface).

    Attributes:
        ch4_kg: methane emitted (kg CH4/yr).
        n2o_kg: nitrous oxide emitted (kg N2O/yr).
        co2_kg: carbon dioxide emitted (kg CO2/yr) — soil sink/emissions.
        fluxes: intermediate elementary fluxes (kg N spread, kg VS, ...).
        trace: per-subsystem detail (e.g. {"prp": ..., "solid": ...}).
        model_name: name of the variant used (traceability).
    """

    ch4_kg: float = 0.0
    n2o_kg: float = 0.0
    co2_kg: float = 0.0
    fluxes: Dict[str, float] = field(default_factory=dict)
    trace: Dict[str, Any] = field(default_factory=dict)
    model_name: str = ""


ModelCallable = Callable[[ModelContext], ModelResult]


@dataclass(frozen=True)
class ModelSpec:
    """Registration of a model variant in the registry.

    Attributes:
        slot: slot of the phenomenon (e.g. "enteric_ch4").
        variant: variant name (e.g. "tier2", "tier3_mills").
        tier: IPCC tier level ("Tier-1", "Tier-2", "Tier-3").
        func: callable implementing the universal interface.
        reference: bibliographic reference of the equation.
        description: description of the equation.
    """

    slot: str
    variant: str
    tier: str
    func: ModelCallable
    reference: str
    description: str = ""


class ModelRegistry:
    """Model registry (alternative equations per slot).

    Each slot can hold several variants; a simulation chooses a
    ``model_selection`` = {slot: variant}. Tier-2 and Tier-3 variants
    are thus interchangeable and testable at every simulation.
    """

    def __init__(self) -> None:
        self._specs: Dict[str, Dict[str, ModelSpec]] = {}

    def register(self, spec: ModelSpec) -> None:
        self._specs.setdefault(spec.slot, {})[spec.variant] = spec

    def slots(self) -> List[str]:
        return sorted(self._specs)

    def variants(self, slot: str) -> List[str]:
        return sorted(self._specs.get(slot, {}))

    def get(self, slot: str, variant: Optional[str] = None) -> ModelSpec:
        """Retrieve a variant; if ``variant`` is None, take the first."""
        if slot not in self._specs:
            raise KeyError(f"Unknown slot: {slot}")
        d = self._specs[slot]
        if variant is None:
            variant = next(iter(d))
        if variant not in d:
            raise KeyError(
                f"Unknown variant for {slot}: {variant} "
                f"(available variants: {sorted(d)})"
            )
        return d[variant]

    def selection_summary(
        self, selection: Dict[str, str]
    ) -> Dict[str, Dict[str, str]]:
        """Traceable summary of a model selection (stored as JSON)."""
        out: Dict[str, Dict[str, str]] = {}
        for slot, variant in selection.items():
            spec = self.get(slot, variant)
            out[slot] = {
                "variant": spec.variant,
                "tier": spec.tier,
                "reference": spec.reference,
                "description": spec.description,
            }
        return out


def build_default_registry() -> ModelRegistry:
    """Build the registry with the engine's standard variants.

    Imports the process modules (layer 1) and registers, for each
    slot, the available Tier-2 and Tier-3 variants.

    Returns:
        a complete registry, ready to use.
    """
    from .processes import enteric, manure, soil, carbon, purchases, fieldwork

    reg = ModelRegistry()
    for spec in enteric.SPECS:
        reg.register(spec)
    for spec in manure.SPECS:
        reg.register(spec)
    for spec in soil.SPECS:
        reg.register(spec)
    for spec in carbon.SPECS:
        reg.register(spec)
    for spec in purchases.SPECS:
        reg.register(spec)
    for spec in fieldwork.SPECS:
        reg.register(spec)
    return reg
