"""Case study: 20 ha mixed crop-livestock farm (declarative config).

One file = one case study. Changing the models to test or the
measurements available only requires editing this configuration —
the orchestration lives in pblca.scenarios.run_case_study.

Measurements marked DEMO are illustrative placeholders: replace them
with the actual farm data for a real study.
"""

from pblca.case_study import build_case_study_farm
from pblca.scenarios import (
    CaseStudyConfig,
    GroupMeasurements,
    NumericalOptions,
)

CONFIG = CaseStudyConfig(
    name="ferme_20ha",
    farm_builder=build_case_study_farm,
    measurements=GroupMeasurements(
        # GreenFeed (AHCS) monitoring. DEMO values.
        ch4_ahcs={
            "veaux_0_6mois": 90.0,
            "jeunes_6_12mois": 180.0,
            "engraissés_12_21mois": 260.0,
        },
        ch4_ahcs_rel_sd=0.08,  # DEMO
        # On-farm ration sheets (kg DM/head/day). DEMO values.
        dmi_measured={
            "veaux_0_6mois": 4.2,
            "jeunes_6_12mois": 7.4,
            "engraissés_12_21mois": 10.2,
        },
        ration_rel_sd=0.10,  # DEMO quantification error
        # INRA diet characterisation (dMO, not dE). DEMO values.
        diet_om={
            "veaux_0_6mois": 0.91,
            "jeunes_6_12mois": 0.91,
            "engraissés_12_21mois": 0.91,
        },
        diet_omd={
            "veaux_0_6mois": 0.72,
            "jeunes_6_12mois": 0.67,
            "engraissés_12_21mois": 0.64,
        },
    ),
    variant_grid={
        "enteric_ch4": [
            "tier2_2006_modelled_ingestion",
            "tier2_2006_ingestion_measured",
            "tier2_2019_modelled_ingestion",
            "tier2_2019_ingestion_measured",
            "tier2_fao_ym_modelled_ingestion",
            "tier2_fao_ym_ingestion_measured",
            "tier3_mills_modelled_ingestion",
            "tier3_mills_ingestion_measured",
            "tier3_sauvant2011_modelled_ingestion",
            "tier3_sauvant2011_ingestion_measured",
            "measured_ahcs",
        ],
        "manure_ch4": ["ipcc_tier2", "tier3_eugene2019"],
    },
    named_combinations={
        # Coherent INRA Tier-3 chain: the digestible OM produces the
        # enteric CH4, the non-digestible OM goes to the manure.
        "inra_tier3": {
            "enteric_ch4": "tier3_sauvant2011_modelled_ingestion",
            "manure_ch4": "tier3_eugene2019",
        },
        # Same chain on the measured rations (ration sheets + GreenFeed
        # monitoring of this farm).
        "inra_tier3_ingestion_measured": {
            "enteric_ch4": "tier3_sauvant2011_ingestion_measured",
            "manure_ch4": "tier3_eugene2019",
        },
    },
    mc=NumericalOptions(n_iterations=500, seed=2024),
)
