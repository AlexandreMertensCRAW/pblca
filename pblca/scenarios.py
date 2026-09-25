"""Scenario grids and case-study configuration (declarative layer).

The models worth testing and the measurements available depend on the
case study (farm, data, research question). This module makes both
DECLARATIVE: one :class:`CaseStudyConfig` describes the farm builder,
the on-farm measurements, which model variants to sweep per slot and
which coherent named combinations to run; the engine-independent
functions of this module execute everything and record one JSON entry
per scenario.

Key behaviour — automatic exclusion: a variant whose
``required_group_fields`` (registry ModelSpec) are not all set on every
animal group of the farm is excluded from the grid with a WARNING in
the diagnostics of the grid result (e.g. ``measured_ahcs`` without
GreenFeed data, ``tier3_sauvant2011`` without diet_om/diet_omd). The
remaining scenarios run normally, so a config can be exchanged between
farms with different data availability.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from .engine import LCAEngine
from .params import ParameterSet
from .registry import FarmContext, ModelRegistry


@dataclass
class GroupMeasurements:
    """On-farm measurements per animal group (all optional).

    Attributes:
        ch4_ahcs: g CH4/head/day measured by an Automated
            Head-Chamber System (GreenFeed) per group key.
        ch4_ahcs_rel_sd: relative sd of the AHCS measurement
            (one value for all groups, or a dict per group key).
        dmi_measured: kg DM/head/day measured on farm (ration sheets).
        ge_measured: MJ GE/head/day measured on farm.
        ration_rel_sd: relative sd of the measured rations
            (quantification error propagated in Monte-Carlo).
        diet_om: kg organic matter per kg DM of the diet
            (per group key).
        diet_omd: kg digestible OM per kg OM of the diet
            (per group key; dMO != dE).
        extra: any other AnimalGroup field, per group key
            (escape hatch for case-specific inputs).
    """

    ch4_ahcs: Optional[Dict[str, float]] = None
    ch4_ahcs_rel_sd: Union[float, Dict[str, float], None] = None
    dmi_measured: Optional[Dict[str, float]] = None
    ge_measured: Optional[Dict[str, float]] = None
    ration_rel_sd: Union[float, Dict[str, float], None] = None
    diet_om: Optional[Dict[str, float]] = None
    diet_omd: Optional[Dict[str, float]] = None
    extra: Optional[Dict[str, Dict[str, Any]]] = None

    def apply(self, farm: FarmContext) -> None:
        """Set the measurements on the animal groups of the farm.

        Groups missing from a dict are left untouched (their field
        stays None); dicts may cover a subset of the groups.
        """
        groups = {g.key: g for g in farm.animals}

        def _set(attr: str, values: Optional[Dict[str, Any]]) -> None:
            if not values:
                return
            for key, value in values.items():
                if key not in groups:
                    raise KeyError(
                        f"Unknown animal group key '{key}' in "
                        f"measurement '{attr}' (groups: {sorted(groups)})"
                    )
                setattr(groups[key], attr, value)

        _set("ch4_measured_ahcs", self.ch4_ahcs)
        _set("dmi_measured", self.dmi_measured)
        _set("ge_measured", self.ge_measured)
        _set("diet_om", self.diet_om)
        _set("diet_omd", self.diet_omd)
        if self.ch4_ahcs_rel_sd is not None:
            sd = self.ch4_ahcs_rel_sd
            if isinstance(sd, dict):
                _set("ch4_measured_ahcs_rel_sd", sd)
            else:
                for key in (self.ch4_ahcs or {}):
                    groups[key].ch4_measured_ahcs_rel_sd = sd
        if self.ration_rel_sd is not None:
            sd = self.ration_rel_sd
            if isinstance(sd, dict):
                _set("ration_rel_sd", sd)
            else:
                for key in groups:
                    if self.dmi_measured and key in self.dmi_measured:
                        groups[key].ration_rel_sd = sd
                    elif self.ge_measured and key in self.ge_measured:
                        groups[key].ration_rel_sd = sd
        if self.extra:
            for key, fields in self.extra.items():
                if key not in groups:
                    raise KeyError(
                        f"Unknown animal group key '{key}' in extra "
                        f"measurements (groups: {sorted(groups)})"
                    )
                for attr, value in fields.items():
                    setattr(groups[key], attr, value)


@dataclass
class NumericalOptions:
    """Monte-Carlo settings of the case study.

    Attributes:
        n_iterations: number of Monte-Carlo iterations.
        seed: random seed (reproducibility; same seed across the
            variants of the grid -> paired draws).
        slots_with_per_group_ch4: slots whose Monte-Carlo entries
            carry per-animal-group CH4 statistics.
    """

    n_iterations: int = 500
    seed: Optional[int] = 2024


@dataclass
class CaseStudyConfig:
    """Declarative description of a case study.

    Attributes:
        name: short name (used in the JSON sim_id prefixes).
        farm_builder: callable (params) -> FarmContext (or a callable
            without arguments); builds the farm of the case study.
        measurements: on-farm measurements applied to the animal
            groups before any simulation (optional).
        variant_grid: variants to sweep per slot, e.g.
            ``{"enteric_ch4": ["tier2_2006", "tier3_sauvant2011"]}``.
            Slots absent from the grid keep the registry default.
        named_combinations: coherent scenarios run in addition to the
            one-slot sweeps, e.g.
            ``{"inra_tier3": {"enteric_ch4": "tier3_sauvant2011",
            "manure_ch4": "tier3_eugene2019"}}``.
        mc: Monte-Carlo options (None = no Monte-Carlo).
    """

    name: str
    farm_builder: Callable[..., FarmContext]
    measurements: Optional[GroupMeasurements] = None
    variant_grid: Optional[Dict[str, List[str]]] = None
    named_combinations: Optional[Dict[str, Dict[str, str]]] = None
    mc: Optional[NumericalOptions] = None


@dataclass
class ScenarioRecord:
    """Outcome of one scenario of the grid.

    Attributes:
        sim_id: identifier of the recorded JSON entry.
        model_selection: {slot: variant} actually run.
        excluded: True if the scenario was skipped (missing
            measurements); in that case no simulation ran.
        reason: why the scenario was excluded.
    """

    sim_id: str
    model_selection: Dict[str, str]
    excluded: bool = False
    reason: str = ""


def _variant_is_runnable(
    registry: ModelRegistry,
    slot: str,
    variant: str,
    farms: Sequence[FarmContext],
) -> Optional[str]:
    """Return None if the variant can run on the farms, else the reason.

    A variant is excluded when one of its ``required_group_fields``
    is not set (None) on at least one animal group of at least one
    farm.
    """
    try:
        spec = registry.get(slot, variant)
    except KeyError as exc:
        return f"unknown variant: {exc}"
    for farm in farms:
        for group in farm.animals:
            for attr in spec.required_group_fields:
                if getattr(group, attr, None) is None:
                    return (
                        f"'{attr}' missing on group '{group.key}' "
                        f"(farm '{farm.farm_id}')"
                    )
    return None


def _sim_id(name: str, selection: Dict[str, str]) -> str:
    """Build a compact, readable sim_id from a model selection."""
    parts = "_".join(f"{slot}={v}" for slot, v in sorted(selection.items()))
    return f"grid_{name}_{parts}" if name else f"grid_{parts}"


def run_scenario_grid(
    engine: LCAEngine,
    config: CaseStudyConfig,
    record: bool = True,
) -> List[ScenarioRecord]:
    """Run the scenario grid of a case-study configuration.

    Scenarios (one JSON entry each):
      * one run per (slot, variant) of ``variant_grid`` (the other
        slots keep the registry default variants);
      * one run per named combination of ``named_combinations``.

    Variants whose required measurements are not available on the
    farm are excluded with a warning (no crash), so a configuration
    can be shared between farms with different data availability.

    Args:
        engine: LCA engine (params + registry + datastore).
        config: case-study configuration.
        record: if True, each scenario is recorded in the datastore.

    Returns:
        the list of scenario records (including the excluded ones,
        for reporting).
    """
    farms = config.farm_builder(engine.params)
    if isinstance(farms, FarmContext):
        farms = [farms]
    if config.measurements is not None:
        for farm in farms:
            config.measurements.apply(farm)

    grid = config.variant_grid or {}
    combos = config.named_combinations or {}

    # (label, selection) pairs: label = None for the one-slot sweeps,
    # combo name for the named combinations.
    scenarios: List[tuple] = []
    for slot, variants in sorted(grid.items()):
        for variant in variants:
            scenarios.append((None, {slot: variant}))
    for name, selection in sorted(combos.items()):
        scenarios.append((name, selection))

    records: List[ScenarioRecord] = []
    for label, selection in scenarios:
        if label is not None:
            sim_id = f"grid_{config.name}_{label}"
        else:
            sim_id = _sim_id(config.name, selection)
        complete: Dict[str, str] = {}
        for slot, variant in selection.items():
            reason = _variant_is_runnable(
                engine.registry, slot, variant, farms
            )
            if reason is not None:
                records.append(
                    ScenarioRecord(
                        sim_id=sim_id,
                        model_selection=dict(selection),
                        excluded=True,
                        reason=f"variant '{variant}' excluded: {reason}",
                    )
                )
                complete = None  # type: ignore[assignment]
                break
            complete[slot] = variant
        if complete is None:
            continue
        engine.run(farms, model_selection=complete, sim_id=sim_id,
                   record=record)
        records.append(
            ScenarioRecord(sim_id=sim_id, model_selection=complete)
        )
    return records


def run_case_study(
    engine: LCAEngine,
    config: CaseStudyConfig,
    record: bool = True,
) -> Dict[str, Any]:
    """Orchestrate a full case study from its configuration.

    Steps:
      1. apply the measurements and run the scenario grid
         (central values, one JSON entry per scenario);
      2. for each enteric variant of the grid, run a Monte-Carlo
         (paired draws: same seed; per-animal-group CH4 statistics
         recorded in the JSON for the R figures);
      3. if measured rations are available, run the paired
         ration comparison (IPCC equations vs measured).

    Args:
        engine: LCA engine.
        config: case-study configuration.
        record: if True, every step records its JSON entries.

    Returns:
        a summary dict (grid records, Monte-Carlo summaries).
    """
    farms = config.farm_builder(engine.params)
    if isinstance(farms, FarmContext):
        farms = [farms]
    if config.measurements is not None:
        for farm in farms:
            config.measurements.apply(farm)

    out: Dict[str, Any] = {"case_study": config.name, "scenarios": []}

    # 1. Central-value scenario grid (with per-variant exclusion).
    records = run_scenario_grid(engine, config, record=record)
    out["scenarios"] = [
        {"sim_id": r.sim_id, "excluded": r.excluded, "reason": r.reason}
        for r in records
    ]

    # 2. Monte-Carlo per enteric variant of the grid.
    mc_options = config.mc
    if mc_options is not None:
        mc_summary = {}
        enteric_variants = (config.variant_grid or {}).get("enteric_ch4", [])
        for variant in enteric_variants:
            runnable = _variant_is_runnable(
                engine.registry, "enteric_ch4", variant, farms
            )
            if runnable is not None:
                continue
            mc = engine.run_monte_carlo(
                farms,
                n_iterations=mc_options.n_iterations,
                seed=mc_options.seed,
                model_selection={"enteric_ch4": variant},
                sim_id=f"mc_{config.name}_enteric_{variant}",
                record=record,
            )
            mc_summary[variant] = mc
        out["monte_carlo"] = {
            v: {
                "gwp100": s["impacts"]["gwp100"],
                "failed_iterations": s["failed_iterations"],
            }
            for v, s in mc_summary.items()
        }

    # 3. Paired ration comparison when measured rations exist.
    has_measured_ration = any(
        g.dmi_measured is not None or g.ge_measured is not None
        for farm in farms for g in farm.animals
    )
    if has_measured_ration:
        try:
            comparison = engine.run_ration_comparison(
                farms,
                n_iterations=(mc_options.n_iterations if mc_options else 500),
                seed=mc_options.seed if mc_options else 2024,
                record=record,
            )
            out["ration_comparison"] = comparison["gwp100"]
        except ValueError as exc:
            out["ration_comparison"] = f"skipped: {exc}"

    return out
