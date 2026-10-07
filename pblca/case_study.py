"""Reference farm: a 20 ha mixed crop-livestock farm.

The farm itself lives in the farm card
``cards/farms/ferme_20ha.toml`` (single source of truth: herd,
parcels, purchases, manure management AND the on-farm measurements
declared on the animal groups they belong to). This module only
loads that card and exposes the historical Python API used by the
tests (:data:`FERME_20HA`, :func:`build_case_study_farm`).

Requested configuration:

* **20 ha** including **13 ha of permanent grassland** on which male
  calves are **purchased at 50 kg** and **fattened up to 600 kg** with
  grass (grazing), concentrates and co-products from the farm's crops;
* **7 ha in rotation**: potato -> rapeseed -> spelt -> oat -> 2 years
  of temporary grassland (a 6-year rotation, each crop present each
  year on 7/6 ha on average — modelled as the "average" year of the
  rotation);
* **animals split by age class**: 0-6 months, 6-12 months and
  12-21 months (21 months = 630 days from 50 kg to 600 kg).

Fattening assumptions (average growth ~0.83 kg/d over 630 d): each
batch of calves goes through the 3 age classes; in a steady state
(overlapping batches), the average annual headcount of each class
equals the number of calves purchased per year x (class duration / 365).
"""

from __future__ import annotations

import os
from typing import Optional

from .card import _load_farm_card
from .farm_spec import FarmSpec, build_farm
from .params import ParameterSet
from .registry import FarmContext

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FARM_CARD = os.path.join(_REPO_DIR, "cards", "farms", "ferme_20ha.toml")

#: The 20 ha reference farm, loaded from its farm card (values only).
FERME_20HA: FarmSpec = _load_farm_card(FARM_CARD)


def build_case_study_farm(params: Optional[ParameterSet] = None) -> FarmContext:
    """Build the 20 ha reference farm from its farm card.

    Args:
        params: parameter set (default: the engine's standard set).

    Returns:
        a complete FarmContext built by the generic
        :func:`pblca.farm_spec.build_farm` from :data:`FERME_20HA`.
    """
    return build_farm(FERME_20HA, params)
