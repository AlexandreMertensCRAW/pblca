"""Case study: a 20 ha mixed crop-livestock farm.

Requested configuration:

* **20 ha** including **13 ha of permanent grassland** on which male
  calves are **purchased at 50 kg** and **fattened up to 600 kg** with
  grass (grazing), concentrates and co-products from the farm's crops;
* **7 ha in rotation**: potato → rapeseed → spelt → oat →
  2 years of temporary grassland (a 6-year rotation, each crop present
  each year on 7/6 ha on average — modelled as the "average" year of
  the rotation);
* **animals split by age class**: 0–6 months, 6–12 months and
  12–21 months (21 months = 630 days from 50 kg to 600 kg).

Fattening assumptions (average growth ~0.83 kg/d over 630 d): each
batch of calves goes through the 3 age classes; in a steady state
(overlapping batches), the average annual headcount of each class
equals the number of calves purchased per year × (class duration / 365).
"""

from __future__ import annotations

from .registry import AnimalGroup, FarmContext, LandParcel
from .params import ParameterSet, build_default_parameter_set

# Rotation over 7 ha: each crop covers 7/6 ha on an annual average.
ROTATION_AREA_PER_CROP = 7.0 / 6.0


def build_case_study_farm(params: ParameterSet | None = None) -> FarmContext:
    """Build the case-study farm context (20 ha).

    Args:
        params: parameter set (default: the engine's standard set).

    Returns:
        a complete FarmContext: 3 age classes of fattened calves,
        13 ha of permanent grassland + 7 ha in rotation (6 crops),
        purchases (calves, concentrates, fertilisers), manure 45 % at
        pasture / 55 % in solid storage.
    """
    # ------------------------------------------------------------------
    # Animals: male calves purchased at 50 kg, fattened up to 600 kg
    # over 21 months (630 d). Growth per class:
    #   0–6 months   (183 d): 50 → 200 kg   (~0.82 kg/d)
    #   6–12 months  (182 d): 200 → 350 kg  (~0.82 kg/d)
    #   12–21 months (265 d): 350 → 600 kg  (~0.94 kg/d)
    # 60 calves purchased/yr → average annual headcounts:
    #   class 1: 60 × 183/365 ≈ 30.0
    #   class 2: 60 × 182/365 ≈ 29.9
    #   class 3: 60 × 265/365 ≈ 43.6
    # ------------------------------------------------------------------
    n_purchased = 60.0
    animals = [
        AnimalGroup(
            key="veaux_0_6mois",
            n_head=n_purchased * 183 / 365,
            bw_start=50, bw_end=200, days=183,
            diet_de=0.70, diet_ge_density=18.45,
            share_concentrate=0.10,  # milk + starter concentrate
            grazing=0.5,
        ),
        AnimalGroup(
            key="jeunes_6_12mois",
            n_head=n_purchased * 182 / 365,
            bw_start=200, bw_end=350, days=182,
            diet_de=0.65, diet_ge_density=18.45,
            share_concentrate=0.20,
            grazing=0.8,
        ),
        AnimalGroup(
            key="engraissés_12_21mois",
            n_head=n_purchased * 265 / 365,
            bw_start=350, bw_end=600, days=265,
            diet_de=0.62, diet_ge_density=18.45,
            share_concentrate=0.35,  # grass + concentrates + co-products
            grazing=0.7,
        ),
    ]

    # ------------------------------------------------------------------
    # Parcels: 13 ha permanent grassland + rotation (7 ha, 6 years)
    # ------------------------------------------------------------------
    v = (params or build_default_parameter_set()).central_values()
    parcels = [
        LandParcel(
            key="prairie_permanente",
            crop="prairie_permanente",
            area=13.0,
            n_synthetic=0.0,          # no mineral fertilisation
            n_excreta_grazing=0.0,    # filled dynamically (manure module)
            fuel_use=v["fuel_mowing"],
            soc_ref=v["soc_ref_temp_moist"],
            flu=v["flu_grassland"],
            fmg=v["fmg_full_tillage"],
            fi=v["fi_medium_input"],
            is_grassland=True,
        ),
    ]
    # Rotation: potato, rapeseed, spelt, oat, temporary grassland ×2.
    rotation = [
        ("pomme_de_terre", 110.0, 0.5, True),   # (crop, synthetic N kgN/ha, lime t/ha, deep tillage)
        ("colza", 140.0, 0.0, True),
        ("epeautre", 90.0, 0.0, True),
        ("avoine", 70.0, 0.0, True),
        ("prairie_temporaire_an1", 0.0, 0.0, False),
        ("prairie_temporaire_an2", 0.0, 0.0, False),
    ]
    for crop, n_syn, lime_t, deep in rotation:
        parcels.append(
            LandParcel(
                key=crop,
                crop=crop,
                area=ROTATION_AREA_PER_CROP,
                n_synthetic=n_syn,
                lime=lime_t * 1000.0,
                fuel_use=(
                    v["fuel_ploughing"] + v["fuel_seed_op"] + v["fuel_harvest"]
                    if deep
                    else v["fuel_tillage_reduced"] + v["fuel_seed_op"] + v["fuel_harvest"]
                ),
                soc_ref=v["soc_ref_temp_moist"],
                flu=v["flu_cropland"] if deep else v["flu_grassland"],
                fmg=v["fmg_reduced_tillage"] if not deep else v["fmg_full_tillage"],
                fi=v["fi_high_input"],
                is_grassland=not deep and "prairie" in crop,
            )
        )

    # ------------------------------------------------------------------
    # Annual purchases
    # ------------------------------------------------------------------
    purchases = {
        # 60 calves purchased at 50 kg liveweight
        "n_calves_purchased_kg_lw": n_purchased * 50.0,
        # Purchased concentrates (supplement beyond farm co-products)
        # ~1.2 t DM per fattened animal on top of grass (typical diet)
        "concentrate_kg_dm": 15000.0,
        # Purchased co-products as a supplement (pulps, brans)
        "coproduct_kg_dm": 8000.0,
        # Synthetic fertilisers: sum over the rotation (kg N/yr)
        "synthetic_n_kg": sum(
            n_syn * ROTATION_AREA_PER_CROP for _, n_syn, _, _ in rotation
        ),
        # Amendments: lime (kg/yr)
        "lime_kg": 0.5 * 1000.0 * ROTATION_AREA_PER_CROP,
        # Phosphate and potash (maintenance, kg P2O5 / K2O per year)
        "p2o5_kg": 30.0 * 7.0,
        "k2o_kg": 40.0 * 7.0,
        # Seeds (kg/yr, rotation crops)
        "seeds_kg": 250.0,
    }

    return FarmContext(
        farm_id="ferme_cas_etude_20ha",
        animals=animals,
        parcels=parcels,
        purchases=purchases,
        # 45 % of excretions at pasture (permanent and temporary
        # grassland), 55 % in buildings -> solid storage -> spreading.
        manure_split={"pasture": 0.45, "solid_storage": 0.55},
        manure_exported=0.0,
        avg_temp=11.0,
    )
