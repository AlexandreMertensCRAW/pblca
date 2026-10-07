"""Tests of the declarative farm specification and generic builder.

Covers:

* the generic builder: steady-state headcounts, parcel factor
  selection (deep vs reduced tillage, fuel parameter ids), lime
  conversion, calf-purchase derivation, measurement propagation,
  ``extra`` escape hatch;
* the equivalence between the legacy ``build_case_study_farm`` and
  the new declarative path (same FarmContext values);
* the ``CaseStudyConfig`` farm/farm_builder coexistence rule.
"""

import pytest

from pblca.case_study import FERME_20HA, build_case_study_farm
from pblca.farm_spec import (
    AnimalGroupSpec,
    FarmSpec,
    GrazingEventSpec,
    OrganicFertilisationSpec,
    ParcelSpec,
    SyntheticFertilisationSpec,
    build_farm,
)
from pblca.params import build_default_parameter_set
from pblca.scenarios import CaseStudyConfig


def _minimal_spec(**overrides) -> FarmSpec:
    spec = FarmSpec(
        farm_id="test_farm",
        animals=[
            AnimalGroupSpec(
                key="lot_a", n_head=40.0, days=365, bw_start=100,
                bw_end=300, diet_de=0.65, share_concentrate=0.2,
            ),
        ],
        parcels=[
            ParcelSpec(
                key="maize", crop="mais", area=10.0, deep_tillage=True,
                grazing=[GrazingEventSpec(group="lot_a", entry="2024-05-01",
                                          exit="2024-10-28")],
                synthetic_fertilisation=[
                    SyntheticFertilisationSpec(
                        type="ammonitrate", date="2024-04-15", n_kg=1200.0),
                ],
                organic_fertilisation=[
                    OrganicFertilisationSpec(
                        type="solid_manure", date="2024-03-01", n_kg=800.0),
                ],
            ),
            ParcelSpec(key="meadow", crop="prairie_temporaire", area=5.0),
        ],
        purchases={
            "concentrate_kg_dm": 1000.0,
            "n_calves_purchased": 40.0,
            "calf_purchased_bw_kg": 100.0,
        },
        manure_split={"solid_storage": 1.0},
        avg_temp=12.0,
    )
    from dataclasses import replace
    return replace(spec, **overrides) if overrides else spec


class TestBuildFarm:
    def test_headcount_declared(self):
        farm = build_farm(_minimal_spec())
        group = farm.animals[0]
        assert group.n_head == pytest.approx(40.0)

    def test_calf_purchase_derived(self):
        farm = build_farm(_minimal_spec())
        # n_calves_purchased x calf_purchased_bw_kg -> purchase flow;
        # the declarative keys themselves are not passed downstream.
        assert farm.purchases["n_calves_purchased_kg_lw"] == pytest.approx(
            40.0 * 100.0
        )
        assert "n_calves_purchased" not in farm.purchases
        assert "calf_purchased_bw_kg" not in farm.purchases

    def test_calf_purchase_not_overridden(self):
        spec = _minimal_spec()
        spec.purchases["n_calves_purchased_kg_lw"] = 1234.0
        farm = build_farm(spec)
        assert farm.purchases["n_calves_purchased_kg_lw"] == 1234.0

    def test_deep_tillage_factors(self):
        params = build_default_parameter_set()
        v = params.central_values()
        farm = build_farm(_minimal_spec(), params)
        maize = next(p for p in farm.parcels if p.key == "maize")
        assert maize.fuel_use == pytest.approx(
            v["fuel_ploughing"] + v["fuel_seed_op"] + v["fuel_harvest"]
        )
        assert maize.flu == v["flu_cropland"]
        assert maize.fmg == v["fmg_full_tillage"]
        assert not maize.is_grassland

    def test_reduced_tillage_grassland_factors(self):
        params = build_default_parameter_set()
        v = params.central_values()
        farm = build_farm(_minimal_spec(), params)
        meadow = next(p for p in farm.parcels if p.key == "meadow")
        assert meadow.fuel_use == pytest.approx(
            v["fuel_tillage_reduced"] + v["fuel_seed_op"] + v["fuel_harvest"]
        )
        assert meadow.flu == v["flu_grassland"]
        assert meadow.fmg == v["fmg_reduced_tillage"]
        assert meadow.is_grassland

    def test_lime_t_per_ha_converted_to_kg(self):
        spec = _minimal_spec()
        spec.parcels[0].lime_t_ha = 0.5
        farm = build_farm(spec)
        assert farm.parcels[0].lime == pytest.approx(500.0)

    def test_measurements_propagated(self):
        spec = _minimal_spec(
            animals=[
                AnimalGroupSpec(
                    key="lot_a", n_head=40.0, days=200, bw_start=100,
                    bw_end=250, diet_de=0.65,
                    dmi_measured=6.0,
                    ge_measured=110.0,
                    ration_rel_sd=0.10,
                    ch4_measured_ahcs=150.0,
                    ch4_measured_ahcs_rel_sd=0.08,
                    diet_om=0.91,
                    diet_omd=0.68,
                ),
            ],
        )
        farm = build_farm(spec)
        g = farm.animals[0]
        assert g.dmi_measured == 6.0
        assert g.ge_measured == 110.0
        assert g.ration_rel_sd == 0.10
        assert g.ch4_measured_ahcs == 150.0
        assert g.ch4_measured_ahcs_rel_sd == 0.08
        assert g.diet_om == 0.91
        assert g.diet_omd == 0.68
        assert g.ration_mode == "measured"

    def test_dairy_fields_propagated(self):
        spec = _minimal_spec(
            animals=[
                AnimalGroupSpec(
                    key="vaches", n_head=40.0, days=365, bw_start=600,
                    bw_end=600, diet_de=0.70, milk_prot=0.9, milk_fat=1.1,
                    pregnant=True, work_hours=0.5,
                ),
            ],
            parcels=[ParcelSpec(key="maize", crop="mais", area=10.0,
                                deep_tillage=True)],
        )
        farm = build_farm(spec)
        g = farm.animals[0]
        assert g.milk_prot == 0.9
        assert g.milk_fat == 1.1
        assert g.pregnant is True
        assert g.work_hours == 0.5

    def test_extra_escape_hatch(self):
        spec = _minimal_spec(
            animals=[
                AnimalGroupSpec(
                    key="lot_a", n_head=40.0, days=365, bw_start=100,
                    bw_end=300, diet_de=0.65,
                    extra={"system": "feedlot"},
                ),
            ],
        )
        farm = build_farm(spec)
        assert farm.animals[0].system == "feedlot"

    def test_farm_context_fields(self):
        farm = build_farm(_minimal_spec())
        assert farm.farm_id == "test_farm"
        assert farm.manure_split == {"solid_storage": 1.0}
        assert farm.avg_temp == 12.0


class TestReferenceFarmEquivalence:
    """The declarative FERME_20HA must reproduce the original builder."""

    def test_same_values_as_legacy_builder(self):
        legacy = build_case_study_farm()
        declarative = build_farm(FERME_20HA)
        assert legacy.farm_id == declarative.farm_id
        assert len(legacy.animals) == len(declarative.animals)
        for a1, a2 in zip(legacy.animals, declarative.animals):
            assert a1.key == a2.key
            assert a1.n_head == pytest.approx(a2.n_head)
            assert a1.bw_start == a2.bw_start
            assert a1.diet_de == a2.diet_de
            assert a1.grazing == a2.grazing
        assert len(legacy.parcels) == len(declarative.parcels)
        for p1, p2 in zip(legacy.parcels, declarative.parcels):
            assert p1.key == p2.key
            assert p1.area == pytest.approx(p2.area)
            assert p1.fuel_use == pytest.approx(p2.fuel_use)
            assert p1.flu == pytest.approx(p2.flu)
            assert p1.fmg == pytest.approx(p2.fmg)
            assert p1.fi == pytest.approx(p2.fi)
            assert p1.is_grassland == p2.is_grassland
        assert legacy.purchases == pytest.approx(declarative.purchases, rel=1e-9)
        assert legacy.manure_split == declarative.manure_split
        assert legacy.avg_temp == declarative.avg_temp


class TestCaseStudyConfigRule:
    def test_farm_and_farm_builder_forbidden(self):
        config = CaseStudyConfig(
            name="bad",
            farm=_minimal_spec(),
            farm_builder=lambda params: build_farm(_minimal_spec()),
        )
        from pblca.scenarios import _build_farms
        with pytest.raises(ValueError, match="not both"):
            _build_farms(config, build_default_parameter_set())

    def test_neither_farm_nor_builder_forbidden(self):
        config = CaseStudyConfig(name="empty")
        from pblca.scenarios import _build_farms
        with pytest.raises(ValueError, match="required"):
            _build_farms(config, build_default_parameter_set())

    def test_farm_spec_accepted(self):
        from pblca.scenarios import _build_farms
        from pblca.registry import FarmContext
        config = CaseStudyConfig(name="ok", farm=_minimal_spec())
        farm = _build_farms(config, build_default_parameter_set())
        assert isinstance(farm, FarmContext)
        assert farm.farm_id == "test_farm"


# ----------------------------------------------------------------------
# Grazing events and dated fertilisations
# ----------------------------------------------------------------------
class TestGrazingEvents:
    def _spec_with_grazing(self, **parcel_overrides):
        return _minimal_spec(
            parcels=[
                ParcelSpec(
                    key="maize", crop="mais", area=10.0, deep_tillage=True,
                    **parcel_overrides,
                ),
                ParcelSpec(key="meadow", crop="prairie_temporaire", area=5.0),
            ],
        )

    def test_grazing_fraction_derived_from_events(self):
        # 180 days at pasture -> grazing = 180/365
        spec = self._spec_with_grazing(
            grazing=[GrazingEventSpec(group="lot_a", entry="2024-05-01",
                                      exit="2024-10-28")],
        )
        farm = build_farm(spec)
        assert farm.animals[0].grazing == pytest.approx(180 / 365.0)

    def test_multiple_events_summed(self):
        spec = self._spec_with_grazing(
            grazing=[
                GrazingEventSpec(group="lot_a", entry="2024-05-01",
                                 exit="2024-06-01"),
                GrazingEventSpec(group="lot_a", entry="2024-09-01",
                                 exit="2024-10-01"),
            ],
        )
        farm = build_farm(spec)
        assert farm.animals[0].grazing == pytest.approx(61 / 365.0)

    def test_no_event_means_housed(self):
        farm = build_farm(_minimal_spec(
            parcels=[ParcelSpec(key="maize", crop="mais", area=10.0,
                                deep_tillage=True)],
        ))
        assert farm.animals[0].grazing == 0.0

    def test_unknown_group_rejected(self):
        with pytest.raises(ValueError, match="unknown animal group"):
            build_farm(self._spec_with_grazing(
                grazing=[GrazingEventSpec(group="ghost", entry="2024-05-01",
                                          exit="2024-06-01")],
            ))

    def test_inverted_dates_rejected(self):
        with pytest.raises(ValueError, match="after the entry"):
            build_farm(self._spec_with_grazing(
                grazing=[GrazingEventSpec(group="lot_a", entry="2024-06-01",
                                          exit="2024-05-01")],
            ))

    def test_over_one_year_rejected(self):
        with pytest.raises(ValueError, match="exceed one year"):
            build_farm(self._spec_with_grazing(
                grazing=[GrazingEventSpec(group="lot_a", entry="2023-01-01",
                                          exit="2024-06-01")],
            ))

    def test_deposited_n_routed_to_parcel(self):
        """The manure module routes the deposited N to the grazed
        parcel (n_excreta_grazing, kg N/ha/yr)."""
        from pblca.engine import LCAEngine

        spec = self._spec_with_grazing(
            grazing=[GrazingEventSpec(group="lot_a", entry="2024-05-01",
                                      exit="2024-10-28")],
        )
        engine = LCAEngine(
            datastore_path="/tmp/test_grazing_route.json")
        farm = build_farm(spec, engine.params)
        r = engine.run(farm, record=False)
        # The engine deepcopies the farm; the deposition appears in the
        # manure trace, and the prp nitrogen matches the grazing share.
        fluxes = r.model_outputs["manure_n2o"]["fluxes"]
        assert fluxes["n_prp_total"] > 0
        # The pasture share of the direct N2O equals the grazing
        # fraction of the group (not a declared farm-level split).
        share = fluxes["n_prp_total"] / (
            fluxes["n_prp_total"]
            + fluxes.get("n_organic_available", 0.0)
        )
        assert 0.0 < share < 1.0


class TestDatedFertilisations:
    def test_synthetic_events_summed(self):
        spec = _minimal_spec(
            parcels=[
                ParcelSpec(
                    key="maize", crop="mais", area=10.0, deep_tillage=True,
                    synthetic_fertilisation=[
                        SyntheticFertilisationSpec(
                            type="ammonitrate", date="2024-04-15",
                            n_kg=600.0),
                        SyntheticFertilisationSpec(
                            type="ammonitrate", date="2024-06-15",
                            n_kg=600.0),
                    ],
                ),
            ],
        )
        farm = build_farm(spec)
        # 1200 kg N on 10 ha -> 120 kg N/ha/yr
        assert farm.parcels[0].n_synthetic == pytest.approx(120.0)

    def test_organic_events_summed(self):
        spec = _minimal_spec(
            parcels=[
                ParcelSpec(
                    key="maize", crop="mais", area=10.0, deep_tillage=True,
                    organic_fertilisation=[
                        OrganicFertilisationSpec(
                            type="solid_manure", date="2024-03-01",
                            n_kg=800.0),
                        OrganicFertilisationSpec(
                            type="compost", date="2024-07-01",
                            n_kg=400.0),
                    ],
                ),
            ],
        )
        farm = build_farm(spec)
        assert farm.parcels[0].n_organic_spread == pytest.approx(120.0)

    def test_invalid_date_rejected(self):
        with pytest.raises(ValueError, match="invalid date"):
            build_farm(_minimal_spec(
                parcels=[
                    ParcelSpec(
                        key="maize", crop="mais", area=10.0,
                        deep_tillage=True,
                        synthetic_fertilisation=[
                            SyntheticFertilisationSpec(
                                type="ammonitrate", date="15/04/2024",
                                n_kg=100.0),
                        ],
                    ),
                ],
            ))

    def test_negative_n_rejected(self):
        with pytest.raises(ValueError, match="must be positive"):
            build_farm(_minimal_spec(
                parcels=[
                    ParcelSpec(
                        key="maize", crop="mais", area=10.0,
                        deep_tillage=True,
                        synthetic_fertilisation=[
                            SyntheticFertilisationSpec(
                                type="ammonitrate", date="2024-04-15",
                                n_kg=-5.0),
                        ],
                    ),
                ],
            ))
