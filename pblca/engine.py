"""Orchestration engine: simulations, Monte-Carlo, JSON storage.

Central role (ISO 14040/14044 compliance):

1. run the processes (layer 1) for one farm or a set of farms, in the
   order of physical dependencies. The manure module computes the spread
   organic nitrogen, redistributed over the parcels BEFORE the soil N2O
   module is called (physical N consistency);
2. fill the gas ledger (layer 2) with full traceability (source, model,
   bibliographic reference);
3. characterise the impacts (layer 3: GWP100, GWP20, GWP*);
4. propagate uncertainties by Monte-Carlo: at each iteration,
   ``ParameterSet.draw`` draws ONE value per parameter, shared by every
   farm and every process using it (farm 1 and farm 2 receive the same
   draw — explicit requirement);
5. store each simulation (one entry) in a structured JSON file.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np

from .gases import GASES, GasLedger
from .impacts import GwpStarInputs, characterize
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
    """

    sim_id: str
    ledger: GasLedger
    impacts: Dict[str, float]
    diagnostics: List[Dict[str, Any]]
    model_selection: Dict[str, str]
    farms: List[str]


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
              "inventory": {...},
              "impacts_kg_co2e": {...},
              "uncertainty": {...},
              "diagnostics": [...]
            }
          ]
        }
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._entries: List[Dict[str, Any]] = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            self._entries = doc.get("simulations", [])

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


def _stats(samples: List[float]) -> Dict[str, float]:
    """Descriptive statistics of a sample (mean, sd, percentiles, n)."""
    a = np.asarray(samples, dtype=float)
    if a.size == 0:
        return {"mean": float("nan"), "sd": float("nan"),
                "p5": float("nan"), "p50": float("nan"),
                "p95": float("nan"), "n": 0}
    return {
        "mean": float(a.mean()),
        "sd": float(a.std(ddof=1)) if a.size > 1 else 0.0,
        "p5": float(np.percentile(a, 5)),
        "p50": float(np.percentile(a, 50)),
        "p95": float(np.percentile(a, 95)),
        "n": int(a.size),
    }


def _set_ration_mode(ration_state: List[tuple], mode: str) -> None:
    """Force the ration-definition mode on the animal groups (in place).

    ``ration_state`` is a snapshot of
    (group, dmi_measured, ge_measured, ration_rel_sd) tuples taken
    before the comparison. ``mode`` is either ``"ipcc"`` (measures
    temporarily removed: the IPCC energy chain is used everywhere) or
    ``"measured"`` (the snapshot values are restored; groups carrying
    measures use them).
    """
    for group, dmi, ge, _rel_sd in ration_state:
        if mode == "ipcc":
            group.dmi_measured = None
            group.ge_measured = None
        else:
            group.dmi_measured = dmi
            group.ge_measured = ge


def _perturb_measured_rations(
    ration_state: List[tuple], rng: "np.random.Generator"
) -> None:
    """Apply the measured-ration quantification error (in place).

    Uncertainty of the on-farm measurements: one multiplicative
    lognormal factor (median 1, sigma = ``ration_rel_sd``) is drawn per
    group and per Monte-Carlo iteration and applied to BOTH the
    measured DMI and the measured GE. Intake and gross energy are
    scaled together: the ratio GE = DMI × diet_ge_density is preserved
    and the perturbation represents a genuine ration-quantification
    error (over/under-estimation of the distributed quantity), not a
    feed-analysis error. Groups without measures or without
    ``ration_rel_sd`` are left untouched.

    Call AFTER ``_set_ration_mode(..., "measured")``; the snapshot
    values are restored by the next ``_set_ration_mode`` call.
    """
    for group, dmi, ge, rel_sd in ration_state:
        if dmi is None and ge is None:
            continue
        if not rel_sd or rel_sd <= 0:
            continue
        factor = float(rng.lognormal(0.0, rel_sd))
        if dmi is not None:
            group.dmi_measured = dmi * factor
        if ge is not None:
            group.ge_measured = ge * factor


def _ration_snapshot(
    farms: Sequence[FarmContext],
) -> List[tuple]:
    """Snapshot of the ration definition of every animal group."""
    return [
        (g, g.dmi_measured, g.ge_measured, g.ration_rel_sd)
        for f in farms for g in f.animals
    ]


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
    ) -> Dict[str, Any]:
        """Uncertainty propagation by Monte-Carlo.

        At each iteration, ``ParameterSet.draw`` draws ONE value per
        parameter; this value is used by every farm and every process
        depending on that parameter (farm 1 and farm 2 share the same
        draw — explicit requirement).

        Args:
            farms: one farm or a list of farms.
            n_iterations: number of iterations (>0).
            model_selection: variants per slot.
            seed: random seed (reproducibility).
            record: if True, records a summary entry in the datastore.

        Returns:
            statistics per indicator {mean, sd, p5, p50, p95, n} and
            per gas, plus the central impacts and the model selection.
        """
        if isinstance(farms, FarmContext):
            farms = [farms]
        farms = list(farms)
        rng = np.random.default_rng(seed)

        # Snapshot BEFORE any run: run() mutates n_organic_spread
        # (manure redistribution); the snapshot must capture the
        # original state to restore the parcels at each iteration.
        base_organic = {
            (f.farm_id, p.key): p.n_organic_spread
            for f in farms for p in f.parcels
        }

        def restore_parcels() -> None:
            for f in farms:
                for p in f.parcels:
                    p.n_organic_spread = base_organic[(f.farm_id, p.key)]

        ration_state = _ration_snapshot(farms)
        has_measures = any(
            dmi is not None or ge is not None for _, dmi, ge, _ in ration_state
        )

        # Central simulation (reference). It is recorded through the
        # MC summary entry below (record) — not twice.
        central_values = self.params.central_values()
        central = self.run(
            farms,
            model_selection=model_selection,
            values=central_values,
            sim_id="central",
            record=False,
        )
        summary = self.registry.selection_summary(central.model_selection)
        restore_parcels()

        impact_samples: Dict[str, List[float]] = {k: [] for k in central.impacts}
        gas_samples: Dict[str, List[float]] = {g: [] for g in GASES}
        failed = 0
        for _ in range(n_iterations):
            drawn = self.params.draw(rng)
            restore_parcels()
            if has_measures:
                _perturb_measured_rations(ration_state, rng)
            try:
                it = self.run(
                    farms,
                    model_selection=central.model_selection,
                    values=drawn,
                    record=False,
                )
            except Exception:
                failed += 1
                continue
            for k, v in it.impacts.items():
                impact_samples[k].append(v)
            for g in GASES:
                gas_samples[g].append(it.ledger.total(g))
        restore_parcels()
        _set_ration_mode(ration_state, "measured")

        stats = {k: _stats(v) for k, v in impact_samples.items()}
        gas_stats = {
            g: _stats(v) for g, v in gas_samples.items() if len(v) > 0
        }
        uncertainty = {
            "method": "Monte-Carlo",
            "n_iterations": n_iterations,
            "seed": seed,
            "failed_iterations": failed,
            "impacts": stats,
            "gas_totals_kg": gas_stats,
        }
        if record:
            self._record(central, summary, central_values, uncertainty=uncertainty)
        return {
            "sim_id": "monte_carlo",
            "model_selection": summary,
            "central_impacts": central.impacts,
            **uncertainty,
        }

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
        """Paired evaluation of the two ration-definition modes.

        At each Monte-Carlo iteration, ONE parameter draw is performed
        and the farms are evaluated TWICE with this same draw:

        * ``ipcc_equations``: every group's measured values are
          temporarily removed, so the IPCC energy chain (Eq. 10.3-10.16)
          drives enteric CH4 and manure fluxes;
        * ``measured``: the measured DMI/GE values are restored, so
          enteric CH4 and manure fluxes rely on the farm data.

        Because the two evaluations of an iteration share the same
        parameter draw (and therefore the same EF1, Ym, B0, ...), the
        paired difference between the two modes isolates the effect of
        the additional ration information. The respective standard
        deviations quantify its effect on the precision of the result.

        The measured mode also propagates the quantification error of
        the on-farm measurements: one multiplicative lognormal factor
        per group and per iteration (median 1, sigma = the group's
        ``ration_rel_sd``) is applied to both ``dmi_measured`` and
        ``ge_measured``. With ``ration_rel_sd=None`` (default) the
        measurements are treated as exact and no perturbation is
        applied.

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
            (precision gain: 1 − sd_measured/sd_ipcc).
        """
        if isinstance(farms, FarmContext):
            farms = [farms]
        farms = list(farms)

        # Groups without measurements are identical in both modes; a
        # comparison is only meaningful if at least one group carries
        # measured values.
        ration_state = _ration_snapshot(farms)
        if not any(dmi is not None or ge is not None for _, dmi, ge, _ in ration_state):
            raise ValueError(
                "run_ration_comparison requires at least one animal group "
                "with dmi_measured and/or ge_measured set"
            )

        rng = np.random.default_rng(seed)
        base_organic = {
            (f.farm_id, p.key): p.n_organic_spread
            for f in farms for p in f.parcels
        }

        def restore_parcels() -> None:
            for f in farms:
                for p in f.parcels:
                    p.n_organic_spread = base_organic[(f.farm_id, p.key)]

        modes = ("ipcc_equations", "measured")
        # Map the context ration mode to the switch function argument.
        mode_switch = {"ipcc_equations": "ipcc", "measured": "measured"}

        central_values = self.params.central_values()
        central = {}
        impact_samples: Dict[str, Dict[str, List[float]]] = {
            m: {} for m in modes
        }
        gas_samples: Dict[str, Dict[str, List[float]]] = {
            m: {g: [] for g in GASES} for m in modes
        }
        failed = {m: 0 for m in modes}

        for mode in modes:
            _set_ration_mode(ration_state, mode_switch[mode])
            restore_parcels()
            run = self.run(
                farms,
                model_selection=model_selection,
                values=central_values,
                sim_id=f"central_ration_{mode}",
                record=False,
            )
            central[mode] = run
            impact_samples[mode] = {k: [] for k in run.impacts}

        summary = self.registry.selection_summary(central["measured"].model_selection)

        for _ in range(n_iterations):
            drawn = self.params.draw(rng)
            for mode in modes:
                # The measured mode propagates the quantification error
                # of the on-farm measurements: one lognormal factor per
                # group and per iteration, applied to both DMI and GE.
                # The ipcc mode ignores the measurements entirely, so
                # the pairing on the parameter draw is preserved.
                _set_ration_mode(ration_state, mode_switch[mode])
                if mode == "measured":
                    _perturb_measured_rations(ration_state, rng)
                restore_parcels()
                try:
                    it = self.run(
                        farms,
                        model_selection=central["measured"].model_selection,
                        values=drawn,
                        record=False,
                    )
                except Exception:
                    failed[mode] += 1
                    continue
                for k, v in it.impacts.items():
                    impact_samples[mode][k].append(v)
                for g in GASES:
                    gas_samples[mode][g].append(it.ledger.total(g))

        # Restore the original ration definition (idempotence).
        _set_ration_mode(ration_state, "measured")
        restore_parcels()

        out: Dict[str, Any] = {
            "sim_id": "ration_comparison",
            "method": "paired Monte-Carlo",
            "n_iterations": n_iterations,
            "seed": seed,
            "failed_iterations": failed,
            "model_selection": summary,
        }

        indicators = list(central["measured"].impacts)
        for indicator in indicators:
            ipcc_s = impact_samples["ipcc_equations"][indicator]
            meas_s = impact_samples["measured"][indicator]
            n_pairs = min(len(ipcc_s), len(meas_s))
            paired_diff = [
                meas_s[i] - ipcc_s[i] for i in range(n_pairs)
            ]
            ipcc_stats = _stats(ipcc_s)
            meas_stats = _stats(meas_s)
            sd_ipcc = ipcc_stats["sd"]
            sd_meas = meas_stats["sd"]
            precision_gain = float("nan")
            if sd_ipcc > 0 and not np.isnan(sd_meas):
                precision_gain = 1.0 - sd_meas / sd_ipcc
            out[indicator] = {
                "ipcc_equations": ipcc_stats,
                "measured": meas_stats,
                "paired_difference": _stats(paired_diff),
                "precision_gain_sd": precision_gain,
            }

        for gas in GASES:
            ipcc_s = gas_samples["ipcc_equations"][gas]
            meas_s = gas_samples["measured"][gas]
            if len(ipcc_s) == 0 or len(meas_s) == 0:
                continue
            n_pairs = min(len(ipcc_s), len(meas_s))
            paired_diff = [meas_s[i] - ipcc_s[i] for i in range(n_pairs)]
            ipcc_stats = _stats(ipcc_s)
            meas_stats = _stats(meas_s)
            sd_ipcc = ipcc_stats["sd"]
            sd_meas = meas_stats["sd"]
            precision_gain = float("nan")
            if sd_ipcc > 0 and not np.isnan(sd_meas):
                precision_gain = 1.0 - sd_meas / sd_ipcc
            out[f"{gas}_totals"] = {
                "ipcc_equations": ipcc_stats,
                "measured": meas_stats,
                "paired_difference": _stats(paired_diff),
                "precision_gain_sd": precision_gain,
            }

        if record:
            self._record(
                central["measured"],
                summary,
                central_values,
                uncertainty={
                    k: v for k, v in out.items()
                    if k not in ("sim_id", "method")
                },
            )
        return out
