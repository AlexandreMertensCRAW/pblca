"""Unit and integration tests of the PBLCA engine.

Covers:
* traceable parameters and uncertainty draws,
* model registry (universal interface, Tier-2/Tier-3 variants),
* gas ledger (traceability, totals),
* GWP100 / GWP20 / GWP* indicators,
* engine: central value, reproducible Monte-Carlo, consistency of the
  same parameter draw across farms, JSON storage.
"""

import json
import os

import numpy as np
import pytest

from pblca import (
    GwpStarInputs,
    LCAEngine,
    build_default_parameter_set,
    build_default_registry,
)
from pblca.case_study import build_case_study_farm
from pblca.gases import GasLedger
from pblca.impacts import compute_gwp100, compute_gwpstar
from pblca.registry import DiagLogger, FarmContext, ModelContext


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture()
def engine(tmp_path):
    return LCAEngine(datastore_path=str(tmp_path / "results.json"))


@pytest.fixture()
def farm(engine):
    return build_case_study_farm(engine.params)


# ----------------------------------------------------------------------
# Transversal layer: parameters
# ----------------------------------------------------------------------
class TestParameters:
    def test_reference_traced(self):
        ps = build_default_parameter_set()
        p = ps.get("ef1_soil")
        assert p.reference is not None
        assert "IPCC" in p.reference.source
        assert p.value == pytest.approx(0.01)

    def test_double_definition_forbidden(self):
        from pblca.params import ParameterSet
        ps = ParameterSet()
        ps.add("x", 1.0, "kg")
        with pytest.raises(KeyError):
            ps.add("x", 2.0, "kg")

    def test_unknown_distribution(self):
        from pblca.params import ParameterSet
        ps = ParameterSet()
        with pytest.raises(ValueError):
            ps.add("y", 1.0, "kg", distribution="poisson")

    def test_draw_complete_and_reproducible(self):
        ps = build_default_parameter_set()
        d1 = ps.draw(np.random.default_rng(0))
        d2 = ps.draw(np.random.default_rng(0))
        assert d1 == d2
        assert set(d1) == set(ps.pids())

    def test_central_values(self):
        ps = build_default_parameter_set()
        cv = ps.central_values()
        assert cv["ef1_soil"] == 0.01
        assert len(cv) == len(ps)


# ----------------------------------------------------------------------
# Transversal layer: model registry
# ----------------------------------------------------------------------
class TestRegistry:
    def test_enteric_variants(self):
        reg = build_default_registry()
        variants = reg.variants("enteric_ch4")
        assert "tier2" in variants and "tier3_mills" in variants

    def test_universal_interface(self):
        reg = build_default_registry()
        for slot in reg.slots():
            for variant in reg.variants(slot):
                spec = reg.get(slot, variant)
                assert callable(spec.func)
                assert spec.reference

    def test_unknown_variant(self):
        reg = build_default_registry()
        with pytest.raises(KeyError):
            reg.get("enteric_ch4", "tier99")

    def test_carbon_variants(self):
        reg = build_default_registry()
        assert "rothc_like" in reg.variants("soil_carbon")


# ----------------------------------------------------------------------
# Layer 2: gas ledger
# ----------------------------------------------------------------------
class TestGasLedger:
    def test_unknown_gas_rejected(self):
        ledger = GasLedger()
        with pytest.raises(ValueError):
            ledger.add("NH3", 1.0, "s", "f", "m", "r")

    def test_negative_emission_rejected(self):
        ledger = GasLedger()
        with pytest.raises(ValueError):
            ledger.add("CH4", -5.0, "s", "f", "m", "r")

    def test_co2_sink(self):
        ledger = GasLedger()
        ledger.add_sink(-100.0, "soil_carbon_sink", "f", "m", "r")
        assert ledger.total("CO2") == -100.0

    def test_entry_traceability(self):
        ledger = GasLedger()
        ledger.add("N2O", 2.0, "manure_n2o", "ferme1", "ipcc", "IPCC 2019")
        e = ledger.entries()[0]
        assert e.farm_id == "ferme1"
        assert e.reference == "IPCC 2019"
        assert e.as_dict()["gas"] == "N2O"


# ----------------------------------------------------------------------
# Layer 3: impacts
# ----------------------------------------------------------------------
class TestImpacts:
    def test_gwp100_ar6(self):
        ledger = GasLedger()
        ledger.add("CH4", 1.0, "s", "f", "m", "r")
        ledger.add("N2O", 1.0, "s", "f", "m", "r")
        ledger.add("CO2", 1.0, "s", "f", "m", "r")
        assert compute_gwp100(ledger) == pytest.approx(27.0 + 273.0 + 1.0)

    def test_gwpstar_equilibrium(self):
        # At equilibrium (ΔE = 0), GWP* of CH4 = 0.28 × E.
        ledger = GasLedger()
        ledger.add("CH4", 100.0, "s", "f", "m", "r")
        inputs = GwpStarInputs(ch4_current_kg=100.0, ch4_previous_kg=100.0)
        assert compute_gwpstar(ledger, inputs) == pytest.approx(0.28 * 100.0)

    def test_gwpstar_increase(self):
        # New methane: the flow term dominates (4.53 × ΔE/yr).
        ledger = GasLedger()
        ledger.add("CH4", 100.0, "s", "f", "m", "r")
        inputs = GwpStarInputs(ch4_current_kg=100.0, ch4_previous_kg=0.0, dt_years=20.0)
        expected = 4.53 * (100.0 / 20.0) + 0.28 * 100.0
        assert compute_gwpstar(ledger, inputs) == pytest.approx(expected)

    def test_gwpstar_includes_co2_n2o(self):
        ledger = GasLedger()
        ledger.add("CO2", 10.0, "s", "f", "m", "r")
        ledger.add("N2O", 1.0, "s", "f", "m", "r")
        inputs = GwpStarInputs(ch4_current_kg=0.0)
        assert compute_gwpstar(ledger, inputs) == pytest.approx(10.0 + 273.0)


# ----------------------------------------------------------------------
# Layer 1: processes (physical sanity checks)
# ----------------------------------------------------------------------
class TestProcesses:
    def test_tier3_mills_saturates(self):
        # Mills' equation is capped: CH4(DMI→∞) = a = 10.8 MJ/d.
        from pblca.processes.enteric import enteric_tier3_mills
        from pblca.registry import AnimalGroup

        ps = build_default_parameter_set()
        animal = AnimalGroup(
            key="test", n_head=1, bw_start=400, bw_end=400, days=365,
            diet_de=0.65, diet_ge_density=18.45,
        )
        farm = FarmContext(
            farm_id="t", animals=[animal], parcels=[],
            purchases={}, manure_split={"solid_storage": 1.0},
        )
        ctx = ModelContext(farm, ps, ps.central_values(), DiagLogger())
        res = enteric_tier3_mills(ctx)
        per_head_mj = res.trace["per_group"]["test"]["ch4_mj_day"]
        assert per_head_mj <= 10.8 + 1e-6

    def test_tier2_increases_with_weight(self):
        from pblca.processes.enteric import _energy_chain
        from pblca.registry import AnimalGroup

        ps = build_default_parameter_set()
        values = ps.central_values()
        small = AnimalGroup(
            key="s", n_head=1, bw_start=100, bw_end=150, days=100,
            diet_de=0.65, diet_ge_density=18.45,
        )
        big = AnimalGroup(
            key="b", n_head=1, bw_start=400, bw_end=450, days=100,
            diet_de=0.65, diet_ge_density=18.45,
        )
        farm = FarmContext(
            farm_id="t", animals=[small, big], parcels=[],
            purchases={}, manure_split={"pasture": 1.0},
        )
        ctx = ModelContext(farm, ps, values, DiagLogger())
        e_small = _energy_chain(ctx, small)
        e_big = _energy_chain(ctx, big)
        assert e_big["ge_mj_day"] > e_small["ge_mj_day"]

    def test_measured_ration_bypasses_ipcc(self):
        # Mode "measured": DMI/GE encoded by hand must override the
        # IPCC estimate, for both enteric variants.
        from pblca.processes.enteric import _energy_chain, enteric_tier2
        from pblca.registry import AnimalGroup

        ps = build_default_parameter_set()
        base = AnimalGroup(
            key="g", n_head=1, bw_start=200, bw_end=350, days=182,
            diet_de=0.65, diet_ge_density=18.45,
        )
        measured = AnimalGroup(
            key="g", n_head=1, bw_start=200, bw_end=350, days=182,
            diet_de=0.65, diet_ge_density=18.45,
            dmi_measured=8.0, ge_measured=147.6,
        )
        farm = FarmContext(
            farm_id="t", animals=[base], parcels=[],
            purchases={}, manure_split={"solid_storage": 1.0},
        )
        ctx = ModelContext(farm, ps, ps.central_values(), DiagLogger())
        e_ipcc = _energy_chain(ctx, base)
        assert e_ipcc["ration_mode"] == "ipcc_equations"
        e_meas = _energy_chain(ctx, measured)
        assert e_meas["ration_mode"] == "measured"
        assert e_meas["dmi_kg_day"] == pytest.approx(8.0)
        assert e_meas["ge_mj_day"] == pytest.approx(147.6)
        # CH4 must scale with the measured GE, not the IPCC GE.
        ch4_base = enteric_tier2(ctx).ch4_kg
        ctx.farm.animals = [measured]
        ch4_meas = enteric_tier2(ctx).ch4_kg
        assert ch4_meas != pytest.approx(ch4_base)

    def test_measured_ration_inconsistency_warns(self):
        from pblca.processes.enteric import _energy_chain
        from pblca.registry import AnimalGroup

        ps = build_default_parameter_set()
        measured = AnimalGroup(
            key="g", n_head=1, bw_start=200, bw_end=350, days=182,
            diet_de=0.65, diet_ge_density=18.45,
            dmi_measured=8.0, ge_measured=200.0,  # inconsistent pair
        )
        farm = FarmContext(
            farm_id="t", animals=[measured], parcels=[],
            purchases={}, manure_split={"solid_storage": 1.0},
        )
        log = DiagLogger()
        ctx = ModelContext(farm, ps, ps.central_values(), log)
        _energy_chain(ctx, measured)
        assert any(m["level"] == "WARNING" for m in log.as_list())

    def test_measured_dmi_only_derives_ge(self):
        from pblca.processes.enteric import _energy_chain
        from pblca.registry import AnimalGroup

        ps = build_default_parameter_set()
        measured = AnimalGroup(
            key="g", n_head=1, bw_start=200, bw_end=350, days=182,
            diet_de=0.65, diet_ge_density=18.45,
            dmi_measured=8.0,  # GE derived: 8.0 × 18.45 = 147.6
        )
        farm = FarmContext(
            farm_id="t", animals=[measured], parcels=[],
            purchases={}, manure_split={"solid_storage": 1.0},
        )
        ctx = ModelContext(farm, ps, ps.central_values(), DiagLogger())
        e = _energy_chain(ctx, measured)
        assert e["ge_mj_day"] == pytest.approx(8.0 * 18.45)

    def test_measured_ration_flows_to_manure(self):
        # Enteric ↔ manure consistency: the measured intake must also
        # drive VS and N excretion in the manure models.
        from pblca.processes.manure import _per_group_fluxes
        from pblca.registry import AnimalGroup

        ps = build_default_parameter_set()
        measured = AnimalGroup(
            key="g", n_head=1, bw_start=200, bw_end=350, days=182,
            diet_de=0.65, diet_ge_density=18.45,
            dmi_measured=12.0, ge_measured=221.4,
        )
        farm = FarmContext(
            farm_id="t", animals=[measured], parcels=[],
            purchases={}, manure_split={"solid_storage": 1.0},
        )
        ctx = ModelContext(farm, ps, ps.central_values(), DiagLogger())
        fl = _per_group_fluxes(ctx)
        assert fl["g"]["vs_kg_day"] > 0
        assert fl["g"]["n_excreta_kg_day"] == pytest.approx(
            12.0 * ps.central_values()["cp_feed"] / 6.25
        )

    def test_soil_n2o_proportional_to_inputs(self):
        from pblca.processes.soil import soil_n2o
        from pblca.registry import LandParcel

        ps = build_default_parameter_set()
        parcel = LandParcel(key="p", crop="ble", area=10.0, n_synthetic=100.0)
        farm = FarmContext(
            farm_id="t", animals=[], parcels=[parcel],
            purchases={}, manure_split={},
        )
        ctx = ModelContext(farm, ps, ps.central_values(), DiagLogger())
        r1 = soil_n2o(ctx)
        parcel.n_synthetic = 200.0
        r2 = soil_n2o(ctx)
        assert r2.n2o_kg == pytest.approx(2 * r1.n2o_kg, rel=1e-9)


# ----------------------------------------------------------------------
# Engine: integration
# ----------------------------------------------------------------------
class TestEngine:
    def test_central_value(self, engine, farm):
        r = engine.run(farm, record=False)
        assert r.ledger.total("CH4") > 0
        assert r.ledger.total("N2O") > 0
        assert r.impacts["gwp100"] > 0
        # Order of magnitude: ~30-40 kg CH4 per fattened head per year.
        n_total = sum(a.n_head for a in farm.animals)
        per_head = r.ledger.total("CH4") / n_total
        assert 15 < per_head < 80

    def test_multi_farm_sum(self, engine, farm):
        farm2 = build_case_study_farm(engine.params)
        farm2.farm_id = "ferme_b"
        single = engine.run(farm, record=False)
        both = engine.run([farm, farm2], record=False)
        assert both.ledger.total("CH4") == pytest.approx(
            2 * single.ledger.total("CH4"), rel=1e-9
        )

    def test_parameter_shared_across_farms(self, engine, farm):
        """Requirement: a parameter keeps the same value for all farms
        of a same Monte-Carlo iteration (here: a single EF1)."""
        farm2 = build_case_study_farm(engine.params)
        farm2.farm_id = "ferme_b"
        drawn = {"ef1_soil": 0.02}  # imposed value (2x the central value)
        engine.run(farm, values={**engine.params.central_values(), **drawn}, record=False)
        r = engine.run(
            [farm, farm2],
            values={**engine.params.central_values(), **drawn},
            record=False,
        )
        by_farm = r.ledger.total_by_farm("ferme_cas_etude_20ha")
        by_farm_b = r.ledger.total_by_farm("ferme_b")
        # The two (identical) farms must have identical N2O emissions
        # because EF1 is shared.
        assert by_farm["N2O"] == pytest.approx(by_farm_b["N2O"], rel=1e-12)

    def test_tier2_tier3_different(self, engine, farm):
        r2 = engine.run(farm, model_selection={"enteric_ch4": "tier2"}, record=False)
        r3 = engine.run(farm, model_selection={"enteric_ch4": "tier3_mills"}, record=False)
        # The two variants must differ (but stay of the same order).
        ch4_2, ch4_3 = r2.ledger.total("CH4"), r3.ledger.total("CH4")
        assert ch4_2 != ch4_3
        assert 0.5 < ch4_3 / ch4_2 < 2.0

    def test_monte_carlo_reproducible(self, engine, farm):
        mc1 = engine.run_monte_carlo(farm, n_iterations=50, seed=1, record=False)
        mc2 = engine.run_monte_carlo(farm, n_iterations=50, seed=1, record=False)
        assert mc1["impacts"]["gwp100"]["mean"] == pytest.approx(
            mc2["impacts"]["gwp100"]["mean"]
        )
        assert mc1["failed_iterations"] == 0

    def test_monte_carlo_dispersion(self, engine, farm):
        mc = engine.run_monte_carlo(farm, n_iterations=150, seed=3, record=False)
        s = mc["impacts"]["gwp100"]
        assert s["sd"] > 0
        assert s["p5"] < s["p50"] < s["p95"]
        assert s["n"] == 150

    def test_central_within_mc_interval(self, engine, farm):
        central = engine.run(farm, record=False)
        mc = engine.run_monte_carlo(farm, n_iterations=150, seed=5, record=False)
        s = mc["impacts"]["gwp100"]
        # The central value must stay within [p5, p95] of the MC.
        assert s["p5"] <= central.impacts["gwp100"] <= s["p95"]

    def test_json_storage(self, engine, farm, tmp_path):
        engine.run(farm, sim_id="s1", record=True)
        engine.run_monte_carlo(farm, n_iterations=20, seed=2, record=True)
        path = str(tmp_path / "results.json")
        engine.datastore.save(path)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        assert doc["format_version"] == "1.0"
        assert len(doc["simulations"]) == 2
        entry = doc["simulations"][0]
        assert entry["sim_id"] == "s1"
        assert entry["impacts_kg_co2e"]["gwp100"] > 0
        assert "inventory" in entry and "entries" in entry["inventory"]
        # Traceability: every emission carries its reference.
        e = entry["inventory"]["entries"][0]
        assert e["reference"]

    def test_diagnostics_warning_out_of_domain(self, engine, farm):
        # A very low DE% (REM/REG domain, IPCC 45-85 %) must generate
        # a WARNING and a physical bounding.
        farm.animals[0].diet_de = 0.25
        r = engine.run(farm, record=False)
        assert any(d["level"] == "WARNING" for d in r.diagnostics)

    def test_diagnostics_warning_tier3_dmi(self, engine, farm):
        # Extreme DMI (outside Mills et al. calibration): WARNING.
        # Very high finishing weight + high digestibility => high DMI.
        from pblca.registry import AnimalGroup
        farm.animals = [
            AnimalGroup(
                key="geant", n_head=1, bw_start=1100, bw_end=1300, days=100,
                diet_de=0.85, diet_ge_density=5.0, share_concentrate=0.9,
            )
        ]
        r = engine.run(
            farm, model_selection={"enteric_ch4": "tier3_mills"}, record=False
        )
        assert any(d["level"] == "WARNING" for d in r.diagnostics)

    def test_empty_farm_error(self, engine):
        with pytest.raises(ValueError):
            engine.run([])

    # ------------------------------------------------------------------
    # Paired comparison of the ration-definition modes
    # ------------------------------------------------------------------
    def test_ration_comparison_requires_measures(self, engine, farm):
        # No measured values anywhere: the comparison is meaningless.
        with pytest.raises(ValueError):
            engine.run_ration_comparison(farm, n_iterations=5, record=False)

    def test_ration_comparison_reproducible(self, engine, farm):
        for a in farm.animals:
            a.dmi_measured = 7.0
            a.ge_measured = 7.0 * 18.45
        c1 = engine.run_ration_comparison(
            farm, n_iterations=25, seed=7, record=False
        )
        c2 = engine.run_ration_comparison(
            farm, n_iterations=25, seed=7, record=False
        )
        assert c1["gwp100"]["measured"]["mean"] == pytest.approx(
            c2["gwp100"]["measured"]["mean"]
        )
        assert c1["gwp100"]["ipcc_equations"]["mean"] == pytest.approx(
            c2["gwp100"]["ipcc_equations"]["mean"]
        )
        # Idempotence: the measured values are restored afterwards.
        assert all(a.dmi_measured == 7.0 for a in farm.animals)

    def test_ration_comparison_shared_draw(self, engine, farm):
        """Requirement: the two evaluations of one iteration must share
        the SAME parameter draw (paired comparison)."""
        for a in farm.animals:
            a.dmi_measured = 7.0
            a.ge_measured = 7.0 * 18.45
        c = engine.run_ration_comparison(
            farm, n_iterations=25, seed=7, record=False
        )
        g = c["gwp100"]
        # Identical rations imposed on every group in both modes: the
        # non-enteric part is identical, and the paired difference must
        # be exactly the enteric+manure shift, with n = n_iterations.
        assert g["paired_difference"]["n"] == 25
        assert c["failed_iterations"] == {"ipcc_equations": 0, "measured": 0}
        # No draw lost: both modes ran every iteration.
        assert g["ipcc_equations"]["n"] == 25
        assert g["measured"]["n"] == 25

    def test_ration_comparison_measured_preciser(self, engine, farm):
        """With measured rations, the enteric-energy parameters (Cfi,
        Ca, Ym...) no longer propagate: the sd of GWP100 must shrink."""
        for a in farm.animals:
            a.dmi_measured = 7.0
            a.ge_measured = 7.0 * 18.45
        c = engine.run_ration_comparison(
            farm, n_iterations=150, seed=11, record=False
        )
        g = c["gwp100"]
        sd_ipcc = g["ipcc_equations"]["sd"]
        sd_meas = g["measured"]["sd"]
        assert sd_meas < sd_ipcc
        assert g["precision_gain_sd"] > 0

    def test_measured_ration_uncertainty_propagates(self, engine, farm):
        """The on-farm measurement carries its own quantification
        error (ration_rel_sd): it must widen the sd of the measured
        mode and be reproducible for a given seed."""
        for a in farm.animals:
            a.dmi_measured = 7.0
            a.ge_measured = 7.0 * 18.45
            a.ration_rel_sd = 0.10  # ±10 % quantification error
        c = engine.run_ration_comparison(
            farm, n_iterations=150, seed=13, record=False
        )
        g = c["gwp100"]
        assert g["measured"]["sd"] > 0
        # Idempotence: the original measured values are restored.
        assert all(a.dmi_measured == 7.0 for a in farm.animals)
        assert all(a.ration_rel_sd == 0.10 for a in farm.animals)

    def test_measured_ration_uncertainty_reproducible(self, engine, farm):
        for a in farm.animals:
            a.dmi_measured = 7.0
            a.ge_measured = 7.0 * 18.45
            a.ration_rel_sd = 0.10
        c1 = engine.run_ration_comparison(
            farm, n_iterations=25, seed=17, record=False
        )
        c2 = engine.run_ration_comparison(
            farm, n_iterations=25, seed=17, record=False
        )
        assert c1["gwp100"]["measured"]["mean"] == pytest.approx(
            c2["gwp100"]["measured"]["mean"]
        )

    def test_mc_propagates_ration_uncertainty(self, engine, farm):
        """run_monte_carlo must also propagate the measured-ration
        quantification error (part of the input uncertainty)."""
        for a in farm.animals:
            a.dmi_measured = 7.0
            a.ge_measured = 7.0 * 18.45
            a.ration_rel_sd = 0.15
        mc = engine.run_monte_carlo(
            farm, n_iterations=60, seed=19, record=False
        )
        s = mc["impacts"]["gwp100"]
        assert s["sd"] > 0
        assert s["n"] == 60
        # Idempotence: measured values restored after the MC.
        assert all(a.dmi_measured == 7.0 for a in farm.animals)

    def test_ration_rel_sd_coherence_preserved(self, engine, farm):
        """The quantification error scales DMI and GE together: the
        GE = DMI × density ratio is preserved, so no coherence
        WARNING is emitted during the Monte-Carlo."""
        for a in farm.animals:
            a.dmi_measured = 7.0
            a.ge_measured = 7.0 * 18.45
            a.ration_rel_sd = 0.20
        mc = engine.run_monte_carlo(
            farm, n_iterations=40, seed=23, record=False
        )
        # The central run (unperturbed) is the reference of the entry.
        assert mc["central_impacts"]["gwp100"] > 0
