"""Monte-Carlo uncertainty propagation (sampling sub-layer of layer 1).

Single owner of the Monte-Carlo invariants:

* at each iteration, ``ParameterSet.draw`` draws ONE value per
  parameter, shared by every farm and every process using it
  (cross-occurrence consistency — explicit requirement);
* the measured rations and measured CH4 values are perturbed from a
  snapshot and fully restored after the loop (idempotence).

``LCAEngine.run`` works on deep copies of the farms: the parcel state
(the manure redistribution) is never visible outside a run, so no
parcel save/restore is needed here.

The models of ``pblca/processes`` remain pure evaluators: they read
the drawn values through ``ModelContext`` and never sample anything
themselves. This module produces the values; the processes evaluate
the emissions; ``LCAEngine`` orchestrates and records.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Union

import numpy as np

from .gases import GASES
from .registry import FarmContext

if TYPE_CHECKING:
    from .engine import LCAEngine


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
    (group, dmi_measured, ge_measured, ration_rel_sd, ch4_measures)
    tuples taken before the comparison. ``mode`` is either
    ``"ipcc"`` (ration measures temporarily removed: the IPCC energy
    chain is used everywhere) or ``"measured"`` (the snapshot values
    are restored; groups carrying measures use them). The measured
    enteric-CH4 values are always restored to their snapshot values
    (they are perturbed independently, see
    ``_perturb_measured_rations``).
    """
    for record in ration_state:
        group, dmi, ge, _rel_sd, ch4_measures = (
            record[0], record[1], record[2], record[3], record[4]
        )
        if mode == "ipcc":
            group.dmi_measured = None
            group.ge_measured = None
        else:
            group.dmi_measured = dmi
            group.ge_measured = ge
        for method, value, _sd in ch4_measures:
            setattr(group, f"ch4_measured_{method}", value)


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

    The measured enteric CH4 (``ch4_measured_<method>`` fields) is
    perturbed independently per method with its own lognormal factor
    (sigma = ``ch4_measured_<method>_rel_sd``), so the measurement
    uncertainty of each method propagates through the Monte-Carlo.

    Call AFTER ``_set_ration_mode(..., "measured")``; the snapshot
    values are restored by the next ``_set_ration_mode`` call.
    """
    for record in ration_state:
        group, dmi, ge, rel_sd, ch4_measures = (
            record[0], record[1], record[2], record[3], record[4]
        )
        if (dmi is not None or ge is not None) and rel_sd and rel_sd > 0:
            factor = float(rng.lognormal(0.0, rel_sd))
            if dmi is not None:
                group.dmi_measured = dmi * factor
            if ge is not None:
                group.ge_measured = ge * factor
        for method, value, sd in ch4_measures:
            if value is not None and sd and sd > 0:
                factor = float(rng.lognormal(0.0, sd))
                setattr(group, f"ch4_measured_{method}", value * factor)


def _ration_snapshot(farms: Sequence[FarmContext]) -> List[tuple]:
    """Snapshot of the measured values of every animal group.

    Captures the measured ration (DMI, GE and their relative sd)
    and every measured enteric-CH4 method value (with its relative
    sd), so that the Monte-Carlo perturbations can be applied from
    the unperturbed values and fully restored afterwards.
    """
    from .processes.enteric import CH4_MEASUREMENT_METHODS

    snapshot = []
    for f in farms:
        for g in f.animals:
            ch4_measures = [
                (
                    method,
                    getattr(g, f"ch4_measured_{method}", None),
                    getattr(g, f"ch4_measured_{method}_rel_sd", None),
                )
                for method in CH4_MEASUREMENT_METHODS
            ]
            snapshot.append(
                (g, g.dmi_measured, g.ge_measured, g.ration_rel_sd, ch4_measures)
            )
    return snapshot


def run_monte_carlo(
    engine: "LCAEngine",
    farms: Union[FarmContext, Sequence[FarmContext]],
    n_iterations: int = 1000,
    model_selection: Optional[Dict[str, str]] = None,
    seed: Optional[int] = None,
    record: bool = True,
    return_traces: bool = False,
    sim_id: str = "central",
) -> Dict[str, Any]:
    """Uncertainty propagation by Monte-Carlo.

    At each iteration, ``ParameterSet.draw`` draws ONE value per
    parameter; this value is used by every farm and every process
    depending on that parameter (farm 1 and farm 2 share the same
    draw — explicit requirement).

    Args:
        engine: the LCA engine (runs, records).
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
    if isinstance(farms, FarmContext):
        farms = [farms]
    farms = list(farms)
    rng = np.random.default_rng(seed)

    ration_state = _ration_snapshot(farms)
    has_measures = any(
        (dmi is not None or ge is not None)
        or any(v is not None and sd for _, v, sd in ch4_measures)
        for _, dmi, ge, _, ch4_measures in ration_state
    )

    # Central simulation (reference). It is recorded through the
    # MC summary entry below (record) — not twice.
    central_values = engine.params.central_values()
    central = engine.run(
        farms,
        model_selection=model_selection,
        values=central_values,
        sim_id=sim_id,
        record=False,
    )
    summary = engine.registry.selection_summary(central.model_selection)

    impact_samples: Dict[str, List[float]] = {k: [] for k in central.impacts}
    gas_samples: Dict[str, List[float]] = {g: [] for g in GASES}
    # Per-animal-group enteric CH4 samples (the enteric variant
    # traces ch4_kg per group; a None trace means the selected
    # variant provides no per-group breakdown — e.g. a variant
    # that would fail before tracing, the samples stay empty).
    group_keys = list(
        central.model_outputs.get("enteric_ch4", {})
        .get("trace", {})
        .get("per_group", {})
    )
    group_ch4_samples: Dict[str, List[float]] = {k: [] for k in group_keys}
    # Per-head-per-day enteric CH4 samples (g CH4/head/day, the
    # AHCS measurement unit; derived by the enteric variants from
    # the same trace, so the uncertainty is propagated identically).
    group_ch4_day_samples: Dict[str, List[float]] = {k: [] for k in group_keys}
    # Per-management-system manure CH4 samples (the manure
    # variants trace ch4_kg by storage/handling system: pasture,
    # solid storage, ...).
    system_keys = list(
        central.model_outputs.get("manure_ch4", {})
        .get("trace", {})
        .get("systems", {})
    )
    system_ch4_samples: Dict[str, List[float]] = {
        k: [] for k in system_keys
    }
    failed = 0
    iteration_outputs: List[Dict[str, Any]] = []
    for _ in range(n_iterations):
        drawn = engine.params.draw(rng)
        if has_measures:
            _perturb_measured_rations(ration_state, rng)
        try:
            it = engine.run(
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
        for key in group_keys:
            trace = (
                it.model_outputs.get("enteric_ch4", {})
                .get("trace", {})
                .get("per_group", {})
                .get(key)
            )
            if trace is not None and "ch4_kg" in trace:
                group_ch4_samples[key].append(trace["ch4_kg"])
            if trace is not None and trace.get("ch4_g_day") is not None:
                group_ch4_day_samples[key].append(trace["ch4_g_day"])
        for key in system_keys:
            ch4_sys = (
                it.model_outputs.get("manure_ch4", {})
                .get("trace", {})
                .get("systems", {})
                .get(key)
            )
            if ch4_sys is not None:
                system_ch4_samples[key].append(ch4_sys)
        if return_traces:
            iteration_outputs.append(it.model_outputs)
    _set_ration_mode(ration_state, "measured")

    stats = {k: _stats(v) for k, v in impact_samples.items()}
    gas_stats = {
        g: _stats(v) for g, v in gas_samples.items() if len(v) > 0
    }
    central_groups = (
        central.model_outputs.get("enteric_ch4", {})
        .get("trace", {})
        .get("per_group", {})
    )
    group_stats = {
        k: {
            **_stats(v),
            "central_kg": central_groups.get(k, {}).get("ch4_kg"),
        }
        for k, v in group_ch4_samples.items()
        if len(v) > 0
    }
    group_g_day_stats = {
        k: {
            **_stats(v),
            "central_g_day": central_groups.get(k, {}).get("ch4_g_day"),
        }
        for k, v in group_ch4_day_samples.items()
        if len(v) > 0
    }
    uncertainty = {
        "method": "Monte-Carlo",
        "n_iterations": n_iterations,
        "seed": seed,
        "failed_iterations": failed,
        "impacts": stats,
        "gas_totals_kg": gas_stats,
    }
    if group_stats:
        uncertainty["enteric_ch4_per_group_kg"] = group_stats
    if group_g_day_stats:
        uncertainty["enteric_ch4_per_group_g_day"] = group_g_day_stats
    central_systems = (
        central.model_outputs.get("manure_ch4", {})
        .get("trace", {})
        .get("systems", {})
    )
    system_stats = {
        k: {
            **_stats(v),
            "central_kg": central_systems.get(k),
        }
        for k, v in system_ch4_samples.items()
        if len(v) > 0
    }
    if system_stats:
        uncertainty["manure_ch4_by_system_kg"] = system_stats
    if record:
        engine._record(central, summary, central_values, uncertainty=uncertainty)
    result = {
        "sim_id": "monte_carlo",
        "model_selection": summary,
        "central_impacts": central.impacts,
        **uncertainty,
    }
    if return_traces:
        result["iteration_outputs"] = iteration_outputs
    return result


def run_ration_comparison(
    engine: "LCAEngine",
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
        engine: the LCA engine (runs, records).
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
    if isinstance(farms, FarmContext):
        farms = [farms]
    farms = list(farms)

    # Groups without measurements are identical in both modes; a
    # comparison is only meaningful if at least one group carries
    # measured values.
    ration_state = _ration_snapshot(farms)
    if not any(dmi is not None or ge is not None for _, dmi, ge, _, _ in ration_state):
        raise ValueError(
            "run_ration_comparison requires at least one animal group "
            "with dmi_measured and/or ge_measured set"
        )

    rng = np.random.default_rng(seed)

    modes = ("ipcc_equations", "measured")
    # Map the context ration mode to the switch function argument.
    mode_switch = {"ipcc_equations": "ipcc", "measured": "measured"}
    # Map the requested enteric variant to its ingestion-explicit
    # version so each mode uses the matching ration chain.
    selection_by_mode = {}
    for mode in modes:
        sel = dict(model_selection or {})
        enteric = sel.get("enteric_ch4") or engine.registry.get(
            "enteric_ch4"
        ).variant
        suffix = (
            "_modelled_ingestion"
            if mode == "ipcc_equations"
            else "_measured_ingestion"
        )
        base = enteric
        for tail in ("_modelled_ingestion", "_measured_ingestion"):
            if base.endswith(tail):
                base = base[: -len(tail)]
                break
        sel["enteric_ch4"] = f"{base}{suffix}"
        selection_by_mode[mode] = sel

    central_values = engine.params.central_values()
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
        run = engine.run(
            farms,
            model_selection=selection_by_mode[mode],
            values=central_values,
            sim_id=f"central_ration_{mode}",
            record=False,
        )
        central[mode] = run
        impact_samples[mode] = {k: [] for k in run.impacts}

    summary = engine.registry.selection_summary(central["measured"].model_selection)

    # Paired per-iteration enteric CH4 samples, per group and
    # farm total (the R correlation figure scatter-plots the
    # modelled vs measured draws of the same iteration).
    group_keys = list(
        central["ipcc_equations"]
        .model_outputs.get("enteric_ch4", {})
        .get("trace", {})
        .get("per_group", {})
    )
    enteric_samples: Dict[str, Dict[str, List[float]]] = {
        m: {k: [] for k in [*group_keys, "farm_total"]} for m in modes
    }

    for _ in range(n_iterations):
        drawn = engine.params.draw(rng)
        for mode in modes:
            # The measured mode propagates the quantification error
            # of the on-farm measurements: one lognormal factor per
            # group and per iteration, applied to both DMI and GE.
            # The ipcc mode ignores the measurements entirely, so
            # the pairing on the parameter draw is preserved.
            _set_ration_mode(ration_state, mode_switch[mode])
            if mode == "measured":
                _perturb_measured_rations(ration_state, rng)
            try:
                it = engine.run(
                    farms,
                    model_selection=selection_by_mode[mode],
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
            per_group = (
                it.model_outputs.get("enteric_ch4", {})
                .get("trace", {})
                .get("per_group", {})
            )
            for key in group_keys:
                block = per_group.get(key)
                if block is not None and "ch4_kg" in block:
                    enteric_samples[mode][key].append(block["ch4_kg"])
            enteric_samples[mode]["farm_total"].append(
                it.ledger.total("CH4")
            )

    # Restore the original ration definition (idempotence).
    _set_ration_mode(ration_state, "measured")

    # Trim the samples to complete (paired) iterations only: an
    # iteration with a failed mode contributes to neither side.
    n_pairs = min(
        len(enteric_samples["ipcc_equations"]["farm_total"]),
        len(enteric_samples["measured"]["farm_total"]),
    )
    for mode in modes:
        for key in enteric_samples[mode]:
            enteric_samples[mode][key] = enteric_samples[mode][key][:n_pairs]

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
        # Enteric CH4 per-group paired samples (correlation
        # figure): same length as the farm-total samples.
        if gas == "CH4":
            out["enteric_ch4_samples"] = {
                "ipcc_equations": enteric_samples["ipcc_equations"],
                "measured": enteric_samples["measured"],
            }
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
        engine._record(
            central["measured"],
            summary,
            central_values,
            uncertainty={
                k: v for k, v in out.items()
                if k not in ("sim_id", "method")
            },
        )
    return out


def run_paired_grid(
    engine: "LCAEngine",
    farms: Union[FarmContext, Sequence[FarmContext]],
    slots: Dict[str, Sequence[str]],
    n_iterations: int = 1000,
    model_selection: Optional[Dict[str, str]] = None,
    seed: Optional[int] = None,
    record: bool = True,
    sim_id: str = "paired_grid",
    main_selection: Optional[Dict[str, str]] = None,
    full_factorial: bool = False,
) -> Dict[str, Any]:
    """Paired Monte-Carlo evaluation of model variants over several slots.

    At each Monte-Carlo iteration, ONE parameter draw is performed and
    EVERY column of the grid is evaluated with this same draw. Because
    all columns of one iteration share the parameter values, the
    emissions table is directly comparable column-wise: a row
    difference reflects the model structure, not the sampling noise.

    The measured rations are perturbed per iteration exactly as in
    ``run_monte_carlo`` (one lognormal factor per group, applied to
    both DMI and GE, plus one factor per measured-CH4 method): all
    columns see the SAME perturbed measurements, so the pairing
    holds for the ``_measured_ingestion`` and ``measured_*``
    variants too.

    Two modes:

    * one-factor-at-a-time (default): one column per swept
      (slot, variant), the other swept slots staying at their main
      variant, plus the reference column (every swept slot at its
      main variant);
    * ``full_factorial=True``: one column per combination of the
      swept slots (Cartesian product), e.g. 11 enteric x 2 manure =
      22 paired columns.

    Variants not runnable on the farms (missing required group
    fields) are excluded upfront, with the reason.

    The reference of the paired differences is ``main_selection``
    (one variant per swept slot; default: the first runnable variant
    of each slot). For each swept slot, each non-main variant is
    reported as a PAIRED DIFFERENCE against the reference — in
    full-factorial mode both columns exist in the grid, so the
    difference isolates the pure effect of that slot's model choice
    (parameters, gases, impacts).

    Args:
        engine: the LCA engine (runs, records).
        farms: one farm or a list of farms.
        slots: variants to sweep, per slot, e.g.
            ``{"enteric_ch4": [...], "manure_ch4": [...]}``.
        n_iterations: number of iterations (>0).
        model_selection: variants for the non-swept slots.
        seed: random seed (reproducibility).
        record: if True, records a summary entry in the datastore.
        sim_id: identifier of the recorded entry (use distinct ids
            when running several paired grids).
        main_selection: reference variant per swept slot (default:
            the first runnable variant of each slot).
        full_factorial: sweep the Cartesian product of the variants
            instead of one factor at a time.

    Returns:
        a dictionary with, under ``emissions_table``, one row per
        iteration ``{"iteration": i, "<label>__<indicator>": value,
        ...}`` (flat columns: the whole-farm indicators of every
        column of the grid; indicators = the impacts
        gwp100/gwp20/gwpstar and the gas totals ch4_kg/n2o_kg/co2_kg)
        and, under ``parameter_draws_table``, one row per iteration
        ``{"iteration": i, "<pid>": value, ...}``. The column label is
        the bare variant name when a single slot is swept (contract of
        the enteric-grid analyses), else ``"slot=variant"`` joined by
        commas. Per indicator and column, the descriptive statistics
        are returned under ``farm_indicators_stats`` and the paired
        differences against the reference under
        ``paired_differences_stats``. When a single slot is swept,
        the legacy keys ``slot``, ``variants`` and ``main_variant``
        are also provided (``run_paired_variant_grid`` contract).
    """
    import itertools

    from .scenarios import _variant_is_runnable

    if isinstance(farms, FarmContext):
        farms = [farms]
    farms = list(farms)
    slot_order = list(slots)
    if not slot_order:
        raise ValueError("run_paired_grid requires at least one slot")
    for slot, variants in slots.items():
        if not variants:
            raise ValueError(
                f"run_paired_grid: slot '{slot}' has no variant to sweep"
            )
    single_slot = len(slot_order) == 1

    # Exclude the variants that cannot run on these farms (missing
    # required group fields), keeping a deterministic order.
    runnable: Dict[str, List[str]] = {}
    exclusions: Dict[str, str] = {}
    for slot in slot_order:
        runnable[slot] = []
        for variant in slots[slot]:
            reason = _variant_is_runnable(engine.registry, slot, variant, farms)
            if reason is None:
                runnable[slot].append(variant)
            else:
                label = variant if single_slot else f"{slot}={variant}"
                exclusions[label] = reason
    empty = [s for s in slot_order if not runnable[s]]
    if empty:
        raise ValueError(
            "no runnable variant for slot(s): " + ", ".join(empty)
        )

    # Reference of the paired differences: one variant per swept slot.
    main_sel = dict(main_selection or {})
    for slot in slot_order:
        main_sel.setdefault(slot, runnable[slot][0])
    for slot, variant in main_sel.items():
        if slot not in runnable:
            raise ValueError(
                f"main_selection: unknown swept slot '{slot}' "
                f"(swept: {slot_order})"
            )
        if variant not in runnable[slot]:
            raise ValueError(
                f"main_selection: variant '{variant}' is not among the "
                f"runnable variants of '{slot}': {', '.join(runnable[slot])}"
            )
    reference = {s: main_sel[s] for s in slot_order}

    def _label(sel: Dict[str, str]) -> str:
        if single_slot:
            return sel[slot_order[0]]
        return ",".join(f"{s}={sel[s]}" for s in slot_order)

    # Columns of the grid: (label, model_selection).
    columns: List[tuple] = []
    if full_factorial:
        for combo in itertools.product(*(runnable[s] for s in slot_order)):
            sel = dict(zip(slot_order, combo))
            columns.append((_label(sel), sel))
    else:
        for slot in slot_order:
            for variant in runnable[slot]:
                sel = {**reference, slot: variant}
                columns.append((_label(sel), sel))
        if not single_slot:
            ref_tuple = tuple(reference[s] for s in slot_order)
            existing = {
                tuple(sel[s] for s in slot_order) for _, sel in columns
            }
            if ref_tuple not in existing:
                columns.append((_label(reference), dict(reference)))
        # Single slot: the reference IS the main-variant column.
    labels = [label for label, _ in columns]

    rng = np.random.default_rng(seed)
    ration_state = _ration_snapshot(farms)
    has_measures = any(
        (dmi is not None or ge is not None)
        or any(v is not None and sd for _, v, sd in ch4_measures)
        for _, dmi, ge, _, ch4_measures in ration_state
    )
    base_selection = dict(model_selection or {})
    central_values = engine.params.central_values()
    # Central run (reference values, main selection): used for the
    # summary of the non-swept slots and the source x gas columns.
    central = engine.run(
        farms,
        model_selection={**base_selection, **reference},
        values=central_values,
        sim_id=sim_id,
        record=False,
    )
    summary = engine.registry.selection_summary(central.model_selection)

    indicator_names = sorted(central.impacts)
    gas_names = [g.lower() + "_kg" for g in GASES]
    indicators = [*indicator_names, *gas_names]
    central_sources = central.ledger.total_by_source()
    source_gas_columns: List[str] = []
    for source in sorted(central_sources):
        for gas in sorted(central_sources[source]):
            if central_sources[source][gas]:
                source_gas_columns.append(f"{source}__{gas.lower()}_kg")

    def _farm_indicators(sim) -> Dict[str, float]:
        out_i: Dict[str, float] = {k: float(v) for k, v in sim.impacts.items()}
        for gas in GASES:
            out_i[gas.lower() + "_kg"] = float(sim.ledger.total(gas))
        return out_i

    def _source_gas(sim) -> Dict[str, float]:
        by_source = sim.ledger.total_by_source()
        out_s: Dict[str, float] = {
            col: 0.0 for col in source_gas_columns
        }
        for source in sorted(central_sources):
            for gas, amount in by_source.get(source, {}).items():
                col = f"{source}__{gas.lower()}_kg"
                if col in out_s:
                    out_s[col] = float(amount)
        return out_s

    emissions_table: List[Dict[str, Any]] = []
    parameter_draws_table: List[Dict[str, Any]] = []
    failed: Dict[str, int] = {label: 0 for label in labels}
    reference_label = _label(reference)
    for i in range(n_iterations):
        drawn = engine.params.draw(rng)
        if has_measures:
            _perturb_measured_rations(ration_state, rng)
        row_emissions: Dict[str, Any] = {"iteration": i}
        row_params: Dict[str, Any] = {"iteration": i}
        row_ok = True
        for label, sel in columns:
            try:
                it = engine.run(
                    farms,
                    model_selection={**base_selection, **sel},
                    values=drawn,
                    record=False,
                )
            except Exception:
                failed[label] += 1
                row_ok = False
                break
            values_i = _farm_indicators(it)
            for k in indicators:
                row_emissions[f"{label}__{k}"] = values_i[k]
            if label == reference_label:
                row_emissions.update(_source_gas(it))
        _set_ration_mode(ration_state, "measured")
        if not row_ok:
            continue
        row_params.update(drawn)
        emissions_table.append(row_emissions)
        parameter_draws_table.append(row_params)

    # Per-column statistics of every farm indicator.
    farm_indicators_stats: Dict[str, Dict[str, Any]] = {}
    for k in indicators:
        farm_indicators_stats[k] = {
            label: _stats([row[f"{label}__{k}"] for row in emissions_table])
            for label in labels
        }

    # Paired differences isolating ONE swept slot: alternative column
    # (only that slot differs from the reference) minus the reference
    # column, per iteration — the pure effect of that model choice.
    paired_differences_stats: Dict[str, Dict[str, Any]] = {}
    for slot in slot_order:
        for variant in runnable[slot]:
            if variant == reference[slot]:
                continue
            alt_sel = {**reference, slot: variant}
            alt_label = _label(alt_sel)
            diff_label = variant if single_slot else f"{slot}={variant}"
            paired_differences_stats[diff_label] = {}
            for k in indicators:
                alt_col = [row[f"{alt_label}__{k}"] for row in emissions_table]
                main_col = [
                    row[f"{reference_label}__{k}"] for row in emissions_table
                ]
                paired_differences_stats[diff_label][k] = _stats(
                    [a - m for a, m in zip(alt_col, main_col)]
                )

    central_gwp_factors = {
        pid: value
        for pid, value in central_values.items()
        if pid.startswith("gwp100_") or pid.startswith("gwp20_")
    }
    out: Dict[str, Any] = {
        "sim_id": sim_id,
        "method": "paired Monte-Carlo",
        "mode": "full_factorial" if full_factorial else "one_factor",
        "slots": {s: list(runnable[s]) for s in slot_order},
        "combinations": labels,
        "main_selection": dict(reference),
        "central_gwp_factors": central_gwp_factors,
        "source_gas_columns": source_gas_columns,
        "excluded_variants": exclusions,
        "n_iterations": n_iterations,
        "seed": seed,
        "failed_iterations": failed,
        "model_selection": summary,
        "farm_indicators_stats": farm_indicators_stats,
        "paired_differences_stats": paired_differences_stats,
        "emissions_table": emissions_table,
        "parameter_draws_table": parameter_draws_table,
    }
    if single_slot:
        out["slot"] = slot_order[0]
        out["variants"] = list(runnable[slot_order[0]])
        out["main_variant"] = reference[slot_order[0]]
    if record:
        engine._record(
            central,
            summary,
            central_values,
            uncertainty={k: v for k, v in out.items()
                         if k not in ("sim_id", "method")},
        )
    return out


def run_paired_variant_grid(
    engine: "LCAEngine",
    farms: Union[FarmContext, Sequence[FarmContext]],
    variants: Sequence[str],
    n_iterations: int = 1000,
    model_selection: Optional[Dict[str, str]] = None,
    seed: Optional[int] = None,
    record: bool = True,
    sim_id: str = "paired_enteric_grid",
    main_variant: Optional[str] = None,
) -> Dict[str, Any]:
    """Paired Monte-Carlo evaluation of enteric-CH4 model variants.

    Historical single-slot interface of the paired grid, kept for the
    existing analyses (R scripts, case-study orchestration); it is a
    thin wrapper over :func:`run_paired_grid` with
    ``slots={"enteric_ch4": variants}``. See the full contract there.
    """
    return run_paired_grid(
        engine,
        farms,
        slots={"enteric_ch4": variants},
        n_iterations=n_iterations,
        model_selection=model_selection,
        seed=seed,
        record=record,
        sim_id=sim_id,
        main_selection=(
            {"enteric_ch4": main_variant} if main_variant is not None else None
        ),
    )
