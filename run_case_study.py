"""Demo script: complete case study of the 20 ha farm.

Runs:
  1. the central-value simulation (Tier-2, then Tier-3 Mills),
  2. uncertainty propagation by Monte-Carlo (500 iterations),
  3. a paired comparison of the two ration-definition modes
     (IPCC equations vs measured rations, 500 iterations),
  4. JSON storage (one entry per simulation),
  5. a summary display (inventory per gas + indicators).

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
    # 1. Central values — two variants of enteric methane
    # ------------------------------------------------------------------
    for variant, label in (
        ("tier2", "IPCC Tier-2 (Eq. 10.21)"),
        ("tier3_mills", "Tier-3 Mills et al. 2003 (saturation)"),
    ):
        r = engine.run(
            farm,
            model_selection={"enteric_ch4": variant},
            sim_id=f"central_{variant}",
            record=True,
        )
        print(f"\n--- Enteric methane: {label} ---")
        print(f"  Enteric CH4  : {r.ledger.total('CH4'):10.1f} kg/yr")
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
    # Illustrative on-farm measured intakes (kg DM/head/d per class).
    measured_dmi = {
        "veaux_0_6mois": 4.2,
        "jeunes_6_12mois": 7.4,
        "engraissés_12_21mois": 10.2,
    }
    for a in farm.animals:
        a.dmi_measured = measured_dmi[a.key]
        a.ge_measured = a.dmi_measured * 18.45
    print("\n--- Paired Monte-Carlo: IPCC equations vs measured rations ---")
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
