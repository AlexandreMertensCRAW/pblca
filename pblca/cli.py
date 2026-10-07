"""Command-line interface: one card file, zero user code.

.. code-block:: console

    pblca run etude.toml
    pblca run etude.toml -o autres_resultats.json
    pblca slots
    pblca slots --slot enteric_ch4

``pblca run`` loads the card, validates it against the registry
(fail fast on unknown slot/variant), runs the study (scenario grid,
Monte-Carlo per variant, paired comparisons when applicable) and
saves the JSON datastore with the card metadata embedded (hash +
raw text) for traceability.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from .card import CardError, load_card, run_card


def _cmd_run(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pblca run",
        description="Run a study from its TOML card.",
    )
    parser.add_argument("card", help="path of the TOML study card")
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="results JSON path (default: the card's [datastore] path, "
        "or results.json)",
    )
    args = parser.parse_args(argv)
    try:
        card = load_card(args.card)
    except (OSError, CardError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.output is not None:
        card.datastore_path = args.output
    print("=" * 70)
    print(f"PBLCA — study '{card.name}' (card: {card.path})")
    print("=" * 70)
    summary = run_card(card)
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
                print(f"  {mode:16s}: mean={st['mean']:9.0f}  sd={st['sd']:7.0f}")
            d = g["paired_difference"]
            print(f"  paired difference: mean={d['mean']:9.0f}  sd={d['sd']:7.0f}")
            print(
                f"  precision gain on sd: {g['precision_gain_sd'] * 100:.1f} %"
            )
    print(f"\nResults saved to {summary['datastore_path']} ")
    return 0


def _cmd_slots(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pblca slots",
        description="List the registry slots and their model variants.",
    )
    parser.add_argument(
        "--slot", default=None, help="restrict to one slot (e.g. enteric_ch4)"
    )
    args = parser.parse_args(argv)
    from .registry import build_default_registry

    registry = build_default_registry()
    for slot in registry.slots():
        if args.slot is not None and slot != args.slot:
            continue
        variants = [s.variant for s in registry.get_specs(slot)]
        print(f"{slot}: {', '.join(variants)}")
    return 0


def _cmd_farms(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pblca farms",
        description="List the built-in farms usable in a card.",
    )
    parser.parse_args(argv)
    from .card import BUILTIN_FARMS

    if not BUILTIN_FARMS:
        from .card import _register_builtins

        _register_builtins()
    for name, spec in BUILTIN_FARMS.items():
        print(f"{name}: {spec.farm_id} ({len(spec.animals)} groups, {len(spec.parcels)} parcels)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="pblca",
        description="Process-Based LCA Engine (ISO 14040/14044).",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="run a study from its TOML card")
    sub.add_parser("slots", help="list the registry slots and variants")
    sub.add_parser("farms", help="list the built-in farms")
    args, rest = parser.parse_known_args(argv)
    if args.command == "run":
        return _cmd_run(rest)
    if args.command == "slots":
        return _cmd_slots(rest)
    if args.command == "farms":
        return _cmd_farms(rest)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
