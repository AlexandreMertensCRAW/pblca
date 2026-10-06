"""Case study: 20 ha mixed crop-livestock farm (declarative config).

One file = one case study. The farm is described declaratively
(``farm=FERME_20HA``, a ``FarmSpec`` from ``pblca.case_study``) and the
on-farm measurements (GreenFeed monitoring, ration sheets, INRA diet
characterisation) are declared per animal group directly in the spec —
they are farm data. Changing the models to test only requires editing
this configuration; the orchestration lives in
``pblca.scenarios.run_case_study``.

Measurements marked DEMO are illustrative placeholders: replace them
with the actual farm data for a real study.
"""

from pblca.case_study import FERME_20HA
from pblca.farm_spec import AnimalGroupSpec
from pblca.scenarios import CaseStudyConfig, NumericalOptions

# On-farm measurements, declared on the groups they belong to.
# GreenFeed (AHCS) monitoring g CH4/head/day. DEMO values.
MEASURED_AHCS = {"veaux_0_6mois": 90.0, "jeunes_6_12mois": 180.0,
                 "engraissés_12_21mois": 260.0}
AHCS_REL_SD = 0.08  # DEMO
# On-farm ration sheets (kg DM/head/day). DEMO values.
MEASURED_DMI = {"veaux_0_6mois": 4.2, "jeunes_6_12mois": 7.4,
                "engraissés_12_21mois": 10.2}
RATION_REL_SD = 0.10  # DEMO quantification error
# INRA diet characterisation (dMO, not dE). DEMO values.
DIET_OM = {"veaux_0_6mois": 0.91, "jeunes_6_12mois": 0.91,
           "engraissés_12_21mois": 0.91}
DIET_OMD = {"veaux_0_6mois": 0.72, "jeunes_6_12mois": 0.67,
            "engraissés_12_21mois": 0.64}


def _with_measurements(spec):
    """Return the case-study FarmSpec with the on-farm measurements set
    on the animal groups (dataclasses.replace per group)."""
    from dataclasses import replace

    animals = []
    for a in spec.animals:
        animals.append(replace(
            a,
            ch4_measured_ahcs=MEASURED_AHCS.get(a.key),
            ch4_measured_ahcs_rel_sd=(AHCS_REL_SD
                                     if a.key in MEASURED_AHCS else None),
            dmi_measured=MEASURED_DMI.get(a.key),
            ration_rel_sd=(RATION_REL_SD if a.key in MEASURED_DMI else None),
            diet_om=DIET_OM.get(a.key),
            diet_omd=DIET_OMD.get(a.key),
        ))
    from dataclasses import replace as _r
    return _r(spec, animals=animals)


CONFIG = CaseStudyConfig(
    name="ferme_20ha",
    farm=_with_measurements(FERME_20HA),
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
    main_enteric_variant="tier2_fao_ym_modelled_ingestion",
)
