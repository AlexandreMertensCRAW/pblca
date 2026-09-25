"""Run the 20 ha case study from its declarative configuration.

The farm description, the available measurements, the model variants
to test and the coherent combinations all live in
``case_studies/ferme_20ha.py`` (CaseStudyConfig); this script only
orchestrates: scenario grid -> Monte-Carlo per (slot, variant) of
the grid -> paired ration comparison -> JSON save.
"""

from case_studies.ferme_20ha import CONFIG

from pblca.engine import LCAEngine
from pblca.scenarios import run_case_study


def main() -> None:
    engine = LCAEngine(datastore_path="results.json")

    print("=" * 70)
    print(f"PBLCA — case study '{CONFIG.name}'")
    print("=" * 70)

    summary = run_case_study(engine, CONFIG, record=True)

    print("\n--- Scenario grid (central values) ---")
    for rec in summary["scenarios"]:
        status = "EXCLUDED" if rec["excluded"] else "ok"
        reason = f" — {rec['reason']}" if rec["excluded"] else ""
        print(f"  {rec['sim_id']:75s} [{status}]{reason}")

    if "monte_carlo" in summary:
        print("\n--- Monte-Carlo per variant of the grid (GWP100) ---")
        for variant, s in summary["monte_carlo"].items():
            g = s["gwp100"]
            print(
                f"  {variant:22s} mean={g['mean']:9.0f}  sd={g['sd']:7.0f}"
                f"  p5={g['p5']:9.0f}  p95={g['p95']:9.0f}"
                f"  failed={s['failed_iterations']}"
            )

    if "ration_comparison" in summary:
        print("\n--- Paired comparison: IPCC vs measured rations ---")
        g = summary["ration_comparison"]
        if isinstance(g, str):
            print(f"  {g}")
        else:
            for mode in ("ipcc_equations", "measured"):
                st = g[mode]
                print(
                    f"  {mode:16s}: mean={st['mean']:9.0f}"
                    f"  sd={st['sd']:7.0f}"
                )
            d = g["paired_difference"]
            print(
                f"  paired difference: mean={d['mean']:9.0f}"
                f"  sd={d['sd']:7.0f}"
            )
            print(
                f"  precision gain on sd: "
                f"{g['precision_gain_sd'] * 100:.1f} %"
            )

    engine.datastore.save("results.json")
    print(
        f"\nResults saved to results.json "
        f"({len(engine.datastore)} simulations)."
    )


if __name__ == "__main__":
    main()
