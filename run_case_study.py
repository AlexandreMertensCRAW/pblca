"""Demo script: complete case study of the 20 ha farm.

Runs:
  1. central-value simulations of ALL variants registered for the
     ``enteric_ch4`` and ``manure_ch4`` slots (demonstration of the
     model registry: Tier-2, Tier-3 and on-farm measurement variants,
     each traced to its bibliographic reference),
  2. uncertainty propagation by Monte-Carlo (500 iterations),
  3. a paired comparison of the two ration-definition modes
     (IPCC equations vs measured rations, 500 iterations),
  4. JSON storage (one entry per simulation),
  5. a summary display (inventory per gas + indicators).

Values marked "DEMO" in the comments are illustrative values
invented for this demonstration — replace them with real farm data
for an actual study.

Usage:

.. code-block:: bash

    python3 run_case_study.py
"""

from __future__ import annotations

from pblca import LCAEngine
from pblca.case_study import build_case_study_farm


def main() -> None:
    engine = LCAEngine(datastore_path="results.json")
    farm = build_case_study_farm(engine.params)

    print("=" * 70)
    print("PBLCA — 20 ha mixed crop-livestock farm (case study)")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. Central values — ALL variants of the enteric_ch4 slot
    # ------------------------------------------------------------------
    print("\n--- Enteric methane: all registered variants (central values) ---")
    print(
        f"  {'variant':22s} {'tier':9s} {'CH4 (kg/yr)':>12s} "
        f"{'GWP100':>10s} {'GWP20':>10s} {'GWP*':>10s} {'warn':>5s}"
    )
    for spec in engine.registry.get_specs("enteric_ch4"):
        # The measured_ahcs variant needs on-farm measurements on the
        # groups; set them for this run, then restore (DEMO values:
        # illustrative GreenFeed monitoring results, ± 8 % relative
        # uncertainty — replace with real farm data).
        ahcs_values = {  # DEMO
            "veaux_0_6mois": 90.0,
            "jeunes_6_12mois": 180.0,
            "engraissés_12_21mois": 260.0,
        }
        saved = {}
        if spec.variant == "measured_ahcs":
            for a in farm.animals:
                saved[a.key] = (
                    a.ch4_measured_ahcs, a.ch4_measured_ahcs_rel_sd
                )
                a.ch4_measured_ahcs = ahcs_values[a.key]
                a.ch4_measured_ahcs_rel_sd = 0.08  # DEMO
        r = engine.run(
            farm,
            model_selection={"enteric_ch4": spec.variant},
            sim_id=f"central_{spec.variant}",
            record=True,
        )
        n_warn = sum(1 for m in r.diagnostics if m["level"] == "WARNING")
        enteric_ch4 = sum(
            e.amount_kg for e in r.ledger.entries()
            if e.source == "enteric" and e.gas == "CH4"
        )
        print(
            f"  {spec.variant:22s} {spec.tier:9s} {enteric_ch4:12.1f} "
            f"{r.impacts['gwp100']:10.0f} {r.impacts['gwp20']:10.0f} "
            f"{r.impacts['gwpstar']:10.0f} {n_warn:5d}"
        )
        if spec.variant == "measured_ahcs":
            for a in farm.animals:
                (
                    a.ch4_measured_ahcs,
                    a.ch4_measured_ahcs_rel_sd,
                ) = saved[a.key]

    # ------------------------------------------------------------------
    # 1bis. Central values — ALL variants of the manure_ch4 slot
    # ------------------------------------------------------------------
    print("\n--- Manure methane: all registered variants (central values) ---")
    print(
        f"  {'variant':22s} {'tier':9s} {'CH4 (kg/yr)':>12s} "
        f"{'GWP100':>10s} {'warn':>5s}"
    )
    for spec in engine.registry.get_specs("manure_ch4"):
        r = engine.run(
            farm,
            model_selection={"manure_ch4": spec.variant},
            sim_id=f"central_manure_{spec.variant}",
            record=True,
        )
        n_warn = sum(1 for m in r.diagnostics if m["level"] == "WARNING")
        manure_ch4 = sum(
            e.amount_kg for e in r.ledger.entries()
            if e.source == "manure_ch4" and e.gas == "CH4"
        )
        print(
            f"  {spec.variant:22s} {spec.tier:9s} {manure_ch4:12.1f} "
            f"{r.impacts['gwp100']:10.0f} {n_warn:5d}"
        )

    # ------------------------------------------------------------------
    # 1ter. Full INRA Tier-3 combination (enteric + manure consistent)
    # ------------------------------------------------------------------
    r = engine.run(
        farm,
        model_selection={
            "enteric_ch4": "tier3_sauvant2011",
            "manure_ch4": "tier3_eugene2019",
        },
        sim_id="central_inra_tier3_combo",
        record=True,
    )
    print("\n--- Full INRA Tier-3 combination (enteric + manure) ---")
    print(f"  GWP100: {r.impacts['gwp100']:.0f} kg CO2e")

    # Reference run (kept for the MC section below): tier3_mills.
    r = engine.run(
        farm, model_selection={"enteric_ch4": "tier3_mills"}, record=False
    )
    print(f"\n--- Reference run (Tier-3 Mills, detailed indicators) ---")
    print(f"  GWP100       : {r.impacts['gwp100']:10.0f} kg CO2e")
    print(f"  GWP20        : {r.impacts['gwp20']:10.0f} kg CO2e")
    print(f"  GWP*         : {r.impacts['gwpstar']:10.0f} kg CO2-we")

    # ------------------------------------------------------------------
    # 2. Monte-Carlo (propagated uncertainties, shared parameters)
    # ------------------------------------------------------------------
    print("\n--- Monte-Carlo: 500 iterations (Tier-3) ---")
    mc = engine.run_monte_carlo(
        farm,
        n_iterations=500,
        seed=2024,
        model_selection={"enteric_ch4": "tier3_mills"},
    )
    s = mc["impacts"]["gwp100"]
    print(f"  GWP100 : mean={s['mean']:.0f}  sd={s['sd']:.0f}")
    print(f"           p5={s['p5']:.0f}  median={s['p50']:.0f}  p95={s['p95']:.0f}")
    print(f"  Failed iterations: {mc['failed_iterations']}")

    # ------------------------------------------------------------------
    # 3. Paired comparison: IPCC equations vs measured rations
    # ------------------------------------------------------------------
    # Illustrative on-farm measured intakes (kg DM/head/d per class),
    # with a ±10 % ration-quantification error. DEMO values: replace
    # with real farm ration sheets for an actual study.
    measured_dmi = {  # DEMO
        "veaux_0_6mois": 4.2,
        "jeunes_6_12mois": 7.4,
        "engraissés_12_21mois": 10.2,
    }
    for a in farm.animals:
        a.dmi_measured = measured_dmi[a.key]
        a.ge_measured = a.dmi_measured * 18.45
        a.ration_rel_sd = 0.10
    print("\n--- Paired Monte-Carlo: IPCC equations vs measured rations ---")
    print("    (measured rations carry a ±10 % quantification error)")
    cmp_ = engine.run_ration_comparison(
        farm, n_iterations=500, seed=2024, record=True
    )
    g = cmp_["gwp100"]
    for mode, label in (
        ("ipcc_equations", "IPCC equations"),
        ("measured", "Measured rations"),
    ):
        st = g[mode]
        print(
            f"  {label:18s}: mean={st['mean']:9.0f}  sd={st['sd']:7.0f}"
            f"  p5={st['p5']:9.0f}  p95={st['p95']:9.0f}"
        )
    d = g["paired_difference"]
    print(
        f"  Paired difference : mean={d['mean']:9.0f}  sd={d['sd']:7.0f}"
    )
    print(
        f"  Precision gain on sd (GWP100): {g['precision_gain_sd'] * 100:.1f} %"
    )
    print("  Failed iterations:", cmp_["failed_iterations"])

    # ------------------------------------------------------------------
    # 4. Detailed inventory (layer 2, traceability)
    # ------------------------------------------------------------------
    r = engine.run(farm, model_selection={"enteric_ch4": "tier3_mills"}, record=False)
    print("\n--- Inventory by source (kg/yr) ---")
    for source, gases in sorted(r.ledger.total_by_source().items()):
        parts = [f"{g}={v:.1f}" for g, v in gases.items() if v != 0]
        print(f"  {source:30s} {' | '.join(parts)}")

    # ------------------------------------------------------------------
    # 5. JSON save (one entry per simulation)
    # ------------------------------------------------------------------
    engine.datastore.save("results.json")
    print(f"\nResults saved to results.json ({len(engine.datastore)} simulations).")


if __name__ == "__main__":
    main()
