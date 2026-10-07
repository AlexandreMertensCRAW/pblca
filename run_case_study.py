"""Run the 20 ha case study from its two cards.

The farm (herd, parcels, purchases, manure management and the
on-farm measurements) lives in ``cards/farms/ferme_20ha.toml``; the
model choices (reference selection, variant grid, coherent
combinations, Monte-Carlo plan) live in
``cards/studies/ferme_20ha.toml``. This script only delegates to the
CLI — equivalently:

    pblca run cards/studies/ferme_20ha.toml
"""

from pblca.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "cards/studies/ferme_20ha.toml"]))
