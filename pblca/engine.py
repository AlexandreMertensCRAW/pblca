"""Orchestration engine: simulations and JSON storage.

Central role (ISO 14040/14044 compliance):

1. run the processes (layer 1) for one farm or a set of farms, in the
   order of physical dependencies. The manure module computes the spread
   organic nitrogen, redistributed over the parcels BEFORE the soil N2O
   module is called (physical N consistency);
2. fill the gas ledger (layer 2) with full traceability (source, model,
   bibliographic reference);
3. characterise the impacts (layer 3: GWP100, GWP20, GWP*);
4. propagate uncertainties by Monte-Carlo: the sampling layer
   :mod:`pblca.mc` (single owner of the Monte-Carlo invariants) draws,
   at each iteration, ONE value per parameter via ``ParameterSet.draw``,
   shared by every farm and every process using it (farm 1 and farm 2
   receive the same draw — explicit requirement), while the process
   models remain pure evaluators reading ``ModelContext``;
5. store each simulation (one entry) in a structured JSON file.

``LCAEngine`` is the public façade: ``run_monte_carlo`` and
``run_ration_comparison`` delegate to :mod:`pblca.mc` without any
change of signature or behaviour.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

from .gases import GasLedger
from .impacts import GwpStarInputs, characterize
from .mc import run_monte_carlo as _mc_run_monte_carlo
from .mc import run_ration_comparison as _mc_run_ration_comparison
from .params import ParameterSet, build_default_parameter_set
from .registry import (
    DiagLogger,
    FarmContext,
    ModelContext,
    ModelRegistry,
    build_default_registry,
)

# Slots resolved BEFORE the redistribution of organic nitrogen (buildings).
_PRE_MANURE_SLOTS = ["enteric_ch4", "manure_ch4", "manure_n2o"]
# Slots resolved AFTER redistribution (soil and uncoupled subsystems).
_POST_MANURE_SLOTS = ["soil_n2o", "soil_carbon", "purchases", "fieldwork"]

# Inventory subsystem and gas per slot.
SLOT_SOURCE_GAS = {
    "enteric_ch4": ("enteric", "CH4"),
    "manure_ch4": ("manure_ch4", "CH4"),
    "manure_n2o": ("manure_n2o", "N2O"),
    "soil_n2o": ("soil_n2o", "N2O"),
    "soil_carbon": ("soil_carbon_sink_or_source", "CO2"),
    "purchases": ("purchases_upstream", "CO2"),
    "fieldwork": ("fieldwork_fuel", "CO2"),
}


@dataclass
class SimulationResult:
    """Complete result of a simulation (central value or 1 MC iteration).

    Attributes:
        sim_id: unique identifier.
        ledger: gas ledger (layer 2, full traceability).
        impacts: layer-3 indicators (kg CO2e / kg CO2-we).
        diagnostics: warnings and errors encountered.
        model_selection: variants used (per slot).
        farms: identifiers of the simulated farms.
        model_outputs: per-slot intermediate model outputs
            {slot: {"variant": ..., "trace": ..., "fluxes": ...}} —
            the detailed computations of each model (per-group DMI,
            Ym, DOMI, VS per system, ...) are preserved here instead
            of being discarded after the total is ledgered (ISO
            14044 §4.5, documentation of the data). With several
            farms, the last farm's outputs are kept per slot.
    """

    sim_id: str
    ledger: GasLedger
    impacts: Dict[str, float]
    diagnostics: List[Dict[str, Any]]
    model_selection: Dict[str, str]
    farms: List[str]
    model_outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class DataStore:
    """Structured JSON storage: one entry per simulation.

    File format:

    .. code-block:: json

        {
          "format_version": "1.0",
          "simulations": [
            {
              "sim_id": "...", "timestamp": "...", "farms": [...],
              "model_selection": {...},
              "model_outputs": {...},
              "inventory": {...},
              "impacts_kg_co2e": {...},
              "uncertainty": {...},
              "diagnostics": [...]
            }
          ]
        }

    Lifecycle of the file: when an existing results file is found at
    initialisation, it is MOVED to the archive directory (default
    ``results_archive/``, next to the results file) under the name
    ``results_<timestamp>.json`` before the new run starts appending.
    Each run therefore writes a fresh file, so the R scripts (which
    keep the most recent entry per variant) can never mix two runs,
    and no previous result is ever lost (ISO 14044 §4.5, documentation
    of the data). Set ``archive_previous=False`` to restore the
    append-to-existing-file behaviour.
    """

    def __init__(
        self, path: str, archive_previous: bool = True
    ) -> None:
        self.path = path
        self._entries: List[Dict[str, Any]] = []
        if os.path.exists(path):
            if archive_previous:
                self._archive(path)
            else:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                self._entries = doc.get("simulations", [])

    @staticmethod
    def _archive(path: str) -> str:
        """Move an existing results file to the archive directory.

        The archive directory is ``results_archive`` created next to
        the results file; the archived copy is named
        ``<stem>_<YYYYMMDD_HHMMSS>.json`` so successive runs never
        overwrite each other. Returns the archived path.
        """
        directory = os.path.dirname(os.path.abspath(path))
        stem = os.path.splitext(os.path.basename(path))[0]
        archive_dir = os.path.join(directory, "results_archive")
        os.makedirs(archive_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        archived = os.path.join(archive_dir, f"{stem}_{stamp}.json")
        os.replace(path, archived)
        return archived

    def append(self, entry: Dict[str, Any]) -> None:
        """Append an entry (one simulation) to the in-memory store."""
        self._entries.append(entry)

    def save(self, path: Optional[str] = None) -> None:
        """Write the complete JSON file (memory → disk)."""
        target = path or self.path
        directory = os.path.dirname(os.path.abspath(target))
        os.makedirs(directory or ".", exist_ok=True)
        doc = {"format_version": "1.0", "simulations": self._entries}
        with open(target, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)

    def __len__(self) -> int:
        return len(self._entries)


def _distribute_manure_n(farm: FarmContext, n_organic_total: float) -> None:
    """Distribute the spread organic nitrogen (kg N/yr) over the parcels.

    Distribution proportional to the area eligible for spreading
    (cropland parcels and temporary grasslands, excluding permanent
    grassland whose deposits are already accounted at pasture).
    """
    eligible = [p for p in farm.parcels if not p.is_grassland]
    total_area = sum(p.area for p in eligible)
    if total_area <= 0:
        return
    for p in eligible:
        p.n_organic_spread += n_organic_total * p.area / total_area


class LCAEngine:
    """Process-based LCA engine (multi-farm, Monte-Carlo, registry).

    Attributes:
        params: traceable parameter set with uncertainties.
        registry: model registry (Tier-2/Tier-3 variants per slot).
        datastore: JSON storage of the simulations.
    """

    def __init__(
        self,
        params: Optional[ParameterSet] = None,
        registry: Optional[ModelRegistry] = None,
        datastore_path: str = "results.json",
    ) -> None:
        self.params = params or build_default_parameter_set()
        self.registry = registry or build_default_registry()
        self.datastore = DataStore(datastore_path)

    # ------------------------------------------------------------------
    # Simulation (central value or one Monte-Carlo iteration)
    # ------------------------------------------------------------------
    def run(
        self,
        farms: Union[FarmContext, Sequence[FarmContext]],
        model_selection: Optional[Dict[str, str]] = None,
        values: Optional[Dict[str, float]] = None,
        sim_id: Optional[str] = None,
        record: bool = True,
        ch4_previous_kg: Optional[float] = None,
    ) -> SimulationResult:
        """Run a complete LCA simulation (one iteration).

        Args:
            farms: one farm or a list of farms (parameters are shared
                across farms: the same draw everywhere).
            model_selection: variant per slot (default: first registered).
            values: set of parameter values (default: central values).
                This mechanism guarantees that in Monte-Carlo a
                parameter has the same value everywhere.
            sim_id: simulation identifier (default: random).
            record: if True, appends the entry to the JSON datastore.
            ch4_previous_kg: CH4 emissions at t−Δt for GWP* (default:
                equilibrium, ΔE = 0).

        Returns:
            SimulationResult (gas ledger + impacts + diagnostics).
        """
        if isinstance(farms, FarmContext):
            farms = [farms]
        farms = list(farms)
        if not farms:
            raise ValueError("No farm provided")

        model_selection = dict(model_selection or {})
        for slot in _PRE_MANURE_SLOTS + _POST_MANURE_SLOTS:
            if slot not in model_selection:
                model_selection[slot] = self.registry.get(slot).variant
        summary = self.registry.selection_summary(model_selection)

        values = values if values is not None else self.params.central_values()
        ledger = GasLedger()
        diagnostics = DiagLogger()
        farm_ids: List[str] = []
        model_outputs: Dict[str, Dict[str, Any]] = {}

        # Save parcel state: run() must be idempotent (manure
        # redistribution mutates n_organic_spread in place).
        organic_state = [
            (p, p.n_organic_spread) for f in farms for p in f.parcels
        ]
        try:
            for farm in farms:
                farm_ids.append(farm.farm_id)
                ctx = ModelContext(farm, self.params, values, diagnostics)
                n_organic = 0.0
                # Phase 1: enteric + manure (produces the spread nitrogen).
                for slot in _PRE_MANURE_SLOTS:
                    spec = self.registry.get(slot, model_selection[slot])
                    result = spec.func(ctx)
                    model_outputs[slot] = {
                        "variant": spec.variant,
                        "trace": result.trace,
                        "fluxes": result.fluxes,
                    }
                    source, gas = SLOT_SOURCE_GAS[slot]
                    amount = getattr(result, f"{gas.lower()}_kg")
                    ledger.add(
                        gas, amount, source, farm.farm_id,
                        spec.variant, spec.reference, detail=f"slot={slot}",
                    )
                    if slot == "manure_n2o":
                        n_organic = result.fluxes.get("n_organic_available", 0.0)
                # Redistribution of organic nitrogen to the parcels.
                # Pasture deposits are fully handled by the manure module
                # (EF3PRP + indirect): no transfer to the soil module,
                # to avoid double counting.
                if n_organic > 0:
                    _distribute_manure_n(farm, n_organic)
                # Phase 2: soil (N2O, carbon) + purchases + fieldwork.
                for slot in _POST_MANURE_SLOTS:
                    spec = self.registry.get(slot, model_selection[slot])
                    result = spec.func(ctx)
                    model_outputs[slot] = {
                        "variant": spec.variant,
                        "trace": result.trace,
                        "fluxes": result.fluxes,
                    }
                    source, gas = SLOT_SOURCE_GAS[slot]
                    amount = result.co2_kg if gas == "CO2" else result.n2o_kg
                    if slot == "soil_carbon" and amount < 0:
                        ledger.add_sink(
                            amount, source, farm.farm_id,
                            spec.variant, spec.reference, detail=f"slot={slot}",
                        )
                    else:
                        ledger.add(
                            gas, amount, source, farm.farm_id,
                            spec.variant, spec.reference, detail=f"slot={slot}",
                        )
        finally:
            # Restoration: FarmContext objects remain reusable across
            # simulations (idempotence of run()).
            for parcel, base in organic_state:
                parcel.n_organic_spread = base

        impacts = characterize(
            ledger,
            GwpStarInputs(
                ch4_current_kg=ledger.total("CH4"),
                ch4_previous_kg=ch4_previous_kg,
            ),
        )
        sim = SimulationResult(
            sim_id=sim_id or uuid.uuid4().hex[:12],
            ledger=ledger,
            impacts=impacts,
            diagnostics=diagnostics.as_list(),
            model_selection=model_selection,
            farms=farm_ids,
            model_outputs=model_outputs,
        )
        if record:
            self._record(sim, summary, values, uncertainty=None)
        return sim

    def _record(
        self,
        sim: SimulationResult,
        model_summary: Dict[str, Dict[str, str]],
        values: Dict[str, float],
        uncertainty: Optional[Dict[str, Any]],
    ) -> None:
        """Write the JSON entry of a simulation to the datastore."""
        entry: Dict[str, Any] = {
            "sim_id": sim.sim_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "farms": sim.farms,
            "model_selection": model_summary,
            "model_outputs": sim.model_outputs,
            "inventory": sim.ledger.as_dict(),
            "impacts_kg_co2e": sim.impacts,
            "diagnostics": sim.diagnostics,
        }
        if uncertainty is not None:
            entry["uncertainty"] = uncertainty
        self.datastore.append(entry)

    # ------------------------------------------------------------------
    # Monte-Carlo
    # ------------------------------------------------------------------
    def run_monte_carlo(
        self,
        farms: Union[FarmContext, Sequence[FarmContext]],
        n_iterations: int = 1000,
        model_selection: Optional[Dict[str, str]] = None,
        seed: Optional[int] = None,
        record: bool = True,
        return_traces: bool = False,
        sim_id: str = "central",
    ) -> Dict[str, Any]:
        """Uncertainty propagation by Monte-Carlo (delegates to
        :func:`pblca.mc.run_monte_carlo`).

        At each iteration, ONE value per parameter is drawn and shared
        by every farm and every process using that parameter. See the
        full contract in :mod:`pblca.mc` (single owner of the
        Monte-Carlo invariants: sampling, ration perturbation,
        idempotence).

        Args:
            farms: one farm or a list of farms.
            n_iterations: number of iterations (>0).
            model_selection: variants per slot.
            seed: random seed (reproducibility).
            record: if True, records a summary entry in the datastore.
            sim_id: identifier of the central run written to the
                datastore (use distinct ids when running several
                Monte-Carlos, e.g. one per model variant).
            return_traces: if True, the per-iteration model outputs
                (traces/fluxes of every slot) are returned under the
                "iteration_outputs" key for post-hoc analysis (e.g.
                correlation between DMI and CH4); they are NOT
                written to the JSON datastore (memory: one entry per
                iteration — use with moderate n_iterations).

        Returns:
            statistics per indicator {mean, sd, p5, p50, p95, n} and
            per gas, plus the central impacts and the model selection.
        """
        return _mc_run_monte_carlo(
            self,
            farms,
            n_iterations=n_iterations,
            model_selection=model_selection,
            seed=seed,
            record=record,
            return_traces=return_traces,
            sim_id=sim_id,
        )

    # ------------------------------------------------------------------
    # Paired Monte-Carlo: IPCC equations vs measured rations
    # ------------------------------------------------------------------
    def run_ration_comparison(
        self,
        farms: Union[FarmContext, Sequence[FarmContext]],
        n_iterations: int = 1000,
        model_selection: Optional[Dict[str, str]] = None,
        seed: Optional[int] = None,
        record: bool = True,
    ) -> Dict[str, Any]:
        """Paired evaluation of the two ration-definition modes
        (delegates to :func:`pblca.mc.run_ration_comparison`).

        At each Monte-Carlo iteration, ONE parameter draw is performed
        and the farms are evaluated TWICE with this same draw
        (``ipcc_equations``: IPCC energy chain everywhere;
        ``measured``: on-farm DMI/GE values restored). The paired
        difference isolates the effect of the additional ration
        information; the shared draw guarantees that only the ration
        mode differs between the two evaluations of an iteration. See
        the full contract in :mod:`pblca.mc`.

        Args:
            farms: one farm or a list of farms.
            n_iterations: number of iterations (>0).
            model_selection: variants per slot.
            seed: random seed (reproducibility).
            record: if True, records a summary entry in the datastore.

        Returns:
            a dictionary with, per indicator and per gas: the statistics
            of each mode, the paired difference (measured − ipcc) and
            the relative reduction of the standard deviation
            (precision gain: 1 − sd_measured/sd_ipcc); under
            ``enteric_ch4_samples`` the per-iteration paired enteric CH4
            samples (per group and farm total) used by the R
            correlation figure.
        """
        return _mc_run_ration_comparison(
            self,
            farms,
            n_iterations=n_iterations,
            model_selection=model_selection,
            seed=seed,
            record=record,
        )
