"""Reference case study: a 20 ha mixed crop-livestock farm.

Requested configuration:

* **20 ha** including **13 ha of permanent grassland** on which male
  calves are **purchased at 50 kg** and **fattened up to 600 kg** with
  grass (grazing), concentrates and co-products from the farm's crops;
* **7 ha in rotation**: potato → rapeseed → spelt → oat → 2 years of
  temporary grassland (a 6-year rotation, each crop present each year
  on 7/6 ha on average — modelled as the "average" year of the
  rotation);
* **animals split by age class**: 0–6 months, 6–12 months and
  12–21 months (21 months = 630 days from 50 kg to 600 kg).

Fattening assumptions (average growth ~0.83 kg/d over 630 d): each
batch of calves goes through the 3 age classes; in a steady state
(overlapping batches), the average annual headcount of each class
equals the number of calves purchased per year × (class duration / 365).

The farm is described declaratively by :data:`FERME_20HA`
(:class:`pblca.farm_spec.FarmSpec`, values only); ``build_case_study_farm``
merely calls the generic builder :func:`pblca.farm_spec.build_farm`.
The measurements (GreenFeed monitoring, ration sheets, INRA diet
characterisation) live with the case-study configuration
(``case_studies/ferme_20ha.py``), not here: this module ships the
farm itself, free of any measurement.
"""

from __future__ import annotations

from typing import Optional

from .farm_spec import AnimalGroupSpec, FarmSpec, ParcelSpec, build_farm
from .params import ParameterSet
from .registry import FarmContext

# Rotation over 7 ha: each crop covers 7/6 ha on an annual average.
ROTATION_AREA_PER_CROP = 7.0 / 6.0


FERME_20HA = FarmSpec(
    farm_id="ferme_cas_etude_20ha",
    n_purchased=60.0,
    # 60 calves purchased/yr -> average annual headcounts:
    #   class 1: 60 × 183/365 ≈ 30.0
    #   class 2: 60 × 182/365 ≈ 29.9
    #   class 3: 60 × 265/365 ≈ 43.6
    animals=[
        # 0–6 months (183 d): 50 -> 200 kg (~0.82 kg/d)
        AnimalGroupSpec(
            key="veaux_0_6mois",
            days=183, bw_start=50, bw_end=200,
            diet_de=0.70, share_concentrate=0.10,  # milk + starter concentrate
            grazing=0.5,
            system="mixed",  # barn + pasture, IPCC 2019 Table 10.5
        ),
        # 6–12 months (182 d): 200 -> 350 kg (~0.82 kg/d)
        AnimalGroupSpec(
            key="jeunes_6_12mois",
            days=182, bw_start=200, bw_end=350,
            diet_de=0.65, share_concentrate=0.20,
            grazing=0.8,
            system="mixed",
        ),
        # 12–21 months (265 d): 350 -> 600 kg (~0.94 kg/d)
        AnimalGroupSpec(
            key="engraissés_12_21mois",
            days=265, bw_start=350, bw_end=600,
            diet_de=0.62, share_concentrate=0.35,  # grass + concentrates + co-products
            grazing=0.7,
            system="mixed",
        ),
    ],
    parcels=[
        # 13 ha permanent grassland (grazed + mown, no mineral fertilisation).
        ParcelSpec(
            key="prairie_permanente",
            crop="prairie_permanente",
            area=13.0,
            is_grassland=True,
            fuel_pids=["fuel_mowing"],
            fmg_pid="fmg_full_tillage",
            fi_pid="fi_medium_input",
        ),
        # 7-year rotation crops, each on 7/6 ha (average year).
        # (crop, synthetic N kgN/ha, lime t/ha, deep tillage)
        ParcelSpec(key="pomme_de_terre", crop="pomme_de_terre",
                   area=ROTATION_AREA_PER_CROP,
                   n_synthetic=110.0, lime_t_ha=0.5, deep_tillage=True),
        ParcelSpec(key="colza", crop="colza", area=ROTATION_AREA_PER_CROP,
                   n_synthetic=140.0, deep_tillage=True),
        ParcelSpec(key="epeautre", crop="epeautre", area=ROTATION_AREA_PER_CROP,
                   n_synthetic=90.0, deep_tillage=True),
        ParcelSpec(key="avoine", crop="avoine", area=ROTATION_AREA_PER_CROP,
                   n_synthetic=70.0, deep_tillage=True),
        ParcelSpec(key="prairie_temporaire_an1", crop="prairie_temporaire_an1",
                   area=ROTATION_AREA_PER_CROP),
        ParcelSpec(key="prairie_temporaire_an2", crop="prairie_temporaire_an2",
                   area=ROTATION_AREA_PER_CROP),
    ],
    purchases={
        # Purchased concentrates (supplement beyond farm co-products)
        # ~1.2 t DM per fattened animal on top of grass (typical diet)
        "concentrate_kg_dm": 15000.0,
        # Purchased co-products as a supplement (pulps, brans)
        "coproduct_kg_dm": 8000.0,
        # Synthetic fertilisers: sum over the rotation (kg N/yr)
        "synthetic_n_kg": sum(n * ROTATION_AREA_PER_CROP for n in
                             (110.0, 140.0, 90.0, 70.0, 0.0, 0.0)),
        # Amendments: lime (kg/yr)
        "lime_kg": 0.5 * 1000.0 * ROTATION_AREA_PER_CROP,
        # Phosphate and potash (maintenance, kg P2O5 / K2O per year)
        "p2o5_kg": 30.0 * 7.0,
        "k2o_kg": 40.0 * 7.0,
        # Seeds (kg/yr, rotation crops)
        "seeds_kg": 250.0,
    },
    # 45 % of excretions at pasture (permanent and temporary
    # grassland), 55 % in buildings -> solid storage -> spreading.
    manure_split={"pasture": 0.45, "solid_storage": 0.55},
    manure_exported=0.0,
    avg_temp=11.0,
)


def build_case_study_farm(params: Optional[ParameterSet] = None) -> FarmContext:
    """Build the 20 ha reference farm from its declarative specification.

    Args:
        params: parameter set (default: the engine's standard set).

    Returns:
        a complete FarmContext built by the generic
        :func:`pblca.farm_spec.build_farm` from :data:`FERME_20HA`.
    """
    return build_farm(FERME_20HA, params)
