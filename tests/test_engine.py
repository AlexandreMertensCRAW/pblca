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


@pytest.fixture()
def farm_inra_tier3(engine):
    """Case-study farm with the INRA diet characterisation (dMO, not
    dE) set on every group — required by the tier3_sauvant2011 and
    tier3_eugene2019 variants. DEMO values (feed tables INRA 2018)."""
    farm = build_case_study_farm(engine.params)
    diet = {
        "veaux_0_6mois": (0.91, 0.72),
        "jeunes_6_12mois": (0.91, 0.67),
        "engraissés_12_21mois": (0.91, 0.64),
    }
    for a in farm.animals:
        a.diet_om, a.diet_omd = diet[a.key]
    return farm


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
        assert "tier2_2006" in variants
        assert "tier2_2019" in variants
        assert "tier2_fao_ym" in variants
        assert "tier3_mills" in variants

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
        from pblca.processes.enteric import _energy_chain, enteric_tier2_2006
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
        ch4_base = enteric_tier2_2006(ctx).ch4_kg
        ctx.farm.animals = [measured]
        ch4_meas = enteric_tier2_2006(ctx).ch4_kg
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

    def test_tier2_2006_ym_tabulated(self):
        # IPCC 2006 Table 10.12: two tabulated values, no interpolation.
        # 6.5 % default; 3.0 % only above 90 % concentrates.
        from pblca.processes.enteric import _ym_2006
        from pblca.registry import AnimalGroup, FarmContext, ModelContext

        ps = build_default_parameter_set()
        log = DiagLogger()

        def make(share):
            return AnimalGroup(
                key=f"g{share}", n_head=1, bw_start=200, bw_end=350, days=182,
                diet_de=0.65, diet_ge_density=18.45,
                share_concentrate=share,
            )

        groups = [make(0.0), make(0.35), make(0.70), make(1.0)]
        farm = FarmContext(
            farm_id="t", animals=groups, parcels=[], purchases={},
            manure_split={},
        )
        ctx = ModelContext(farm, ps, ps.central_values(), log)
        v = ctx.v
        yms = [_ym_2006(ctx, v, g) for g in groups]
        assert yms[0] == pytest.approx(0.065)  # default, all shares <= 0.90
        assert yms[1] == pytest.approx(0.065)
        assert yms[2] == pytest.approx(0.065)
        assert yms[3] == pytest.approx(0.030)  # > 90 % concentrates
        # The intermediate zone (50-90 %) is not covered by the source:
        # a WARNING must recommend the 6.5 % default.
        warns = [m for m in log.as_list() if m["level"] == "WARNING"]
        assert len(warns) == 1
        assert "g0.7" in warns[0]["message"]

    def test_tier2_2019_ym_tabulated(self):
        # Tabulated Ym per the 2019 Refinement Table 10.12 (Updated),
        # selected by AnimalGroup.system (no interpolation).
        from pblca.processes.enteric import _ym_2019
        from pblca.registry import AnimalGroup, FarmContext, ModelContext

        ps = build_default_parameter_set()
        log = DiagLogger()

        def make(system, share=0.0):
            return AnimalGroup(
                key=f"g{system}", n_head=1, bw_start=200, bw_end=350,
                days=182, diet_de=0.65, diet_ge_density=18.45,
                share_concentrate=share, system=system,
            )

        groups = [make("grazing"), make("mixed"), make("feedlot")]
        farm = FarmContext(
            farm_id="t", animals=groups, parcels=[], purchases={},
            manure_split={},
        )
        ctx = ModelContext(farm, ps, ps.central_values(), log)
        v = ctx.v
        yms = [_ym_2019(ctx, v, g) for g in groups]
        assert yms[0] == pytest.approx(0.070)
        assert yms[1] == pytest.approx(0.063)
        assert yms[2] == pytest.approx(0.040)
        assert log.n_errors == 0

    def test_tier2_2019_system_inferred_warns(self):
        # system=None: inferred from the concentrate share + WARNING.
        from pblca.processes.enteric import _ym_2019
        from pblca.registry import AnimalGroup, FarmContext, ModelContext

        ps = build_default_parameter_set()
        log = DiagLogger()
        animal = AnimalGroup(
            key="g", n_head=1, bw_start=200, bw_end=350, days=182,
            diet_de=0.65, diet_ge_density=18.45, share_concentrate=0.35,
        )
        farm = FarmContext(
            farm_id="t", animals=[animal], parcels=[], purchases={},
            manure_split={},
        )
        ctx = ModelContext(farm, ps, ps.central_values(), log)
        ym = _ym_2019(ctx, ctx.v, animal)
        assert ym == pytest.approx(0.063)  # mixed inferred
        assert any(m["level"] == "WARNING" for m in log.as_list())

    def test_tier2_2019_unknown_system_error(self):
        # Unknown system: ERROR logged, mixed-system Ym applied.
        from pblca.processes.enteric import _ym_2019
        from pblca.registry import AnimalGroup, FarmContext, ModelContext

        ps = build_default_parameter_set()
        log = DiagLogger()
        animal = AnimalGroup(
            key="g", n_head=1, bw_start=200, bw_end=350, days=182,
            diet_de=0.65, diet_ge_density=18.45, system="unknown_system",
        )
        farm = FarmContext(
            farm_id="t", animals=[animal], parcels=[], purchases={},
            manure_split={},
        )
        ctx = ModelContext(farm, ps, ps.central_values(), log)
        ym = _ym_2019(ctx, ctx.v, animal)
        assert ym == pytest.approx(0.063)
        assert log.n_errors == 1

    def test_tier2_fao_ym_equation(self):
        # Ym(%) = 9.75 - 0.05 × DE%: 6.5 % at DE=65, 6.0 % at DE=75.
        from pblca.processes.enteric import _ym_fao
        from pblca.registry import AnimalGroup, FarmContext, ModelContext

        ps = build_default_parameter_set()
        log = DiagLogger()

        def make(de):
            return AnimalGroup(
                key="g", n_head=1, bw_start=200, bw_end=350, days=182,
                diet_de=de, diet_ge_density=18.45,
            )

        farm = FarmContext(
            farm_id="t", animals=[make(0.65), make(0.75)],
            parcels=[], purchases={}, manure_split={},
        )
        ctx = ModelContext(farm, ps, ps.central_values(), log)
        v = ctx.v
        yms = [_ym_fao(ctx, v, g) for g in farm.animals]
        assert yms[0] == pytest.approx(0.065)
        assert yms[1] == pytest.approx(0.060)

    def test_fao_ym_domain_warning(self):
        # With uncertain parameters drawn far from their central values
        # (steep slope), the FAO Ym can leave the physical domain:
        # a WARNING must be logged and the value bounded.
        from pblca.processes.enteric import _ym_fao
        from pblca.registry import AnimalGroup, FarmContext, ModelContext

        ps = build_default_parameter_set()
        log = DiagLogger()
        animal = AnimalGroup(
            key="g", n_head=1, bw_start=200, bw_end=350, days=182,
            diet_de=0.90, diet_ge_density=18.45,
        )
        farm = FarmContext(
            farm_id="t", animals=[animal], parcels=[], purchases={},
            manure_split={},
        )
        values = dict(ps.central_values())
        values["ym_fao_slope"] = 0.15  # extreme draw: 9.75 − 0.15×90 = −3.75 %
        ctx = ModelContext(farm, ps, values, log)
        ym = _ym_fao(ctx, ctx.v, animal)
        assert ym == pytest.approx(0.015)
        assert any(m["level"] == "WARNING" for m in log.as_list())

    def test_measured_ahcs_variant(self):
        # Direct use of a GreenFeed (AHCS) measurement, g CH4/head/d.
        from pblca.registry import AnimalGroup, FarmContext, ModelContext
        from pblca.processes.enteric import _make_enteric_measured

        ps = build_default_parameter_set()
        log = DiagLogger()
        animal = AnimalGroup(
            key="g", n_head=10, bw_start=200, bw_end=350, days=182,
            diet_de=0.65, diet_ge_density=18.45,
            ch4_measured_ahcs=220.0,
        )
        farm = FarmContext(
            farm_id="t", animals=[animal], parcels=[], purchases={},
            manure_split={},
        )
        ctx = ModelContext(farm, ps, ps.central_values(), log)
        res = _make_enteric_measured("ahcs")(ctx)
        # 220 g/d × 182 d × 10 head = 400.4 kg CH4
        assert res.ch4_kg == pytest.approx(220.0 / 1000.0 * 182 * 10)
        assert res.model_name == "measured_ahcs"
        assert "GreenFeed" in res.trace["method"]

    def test_measured_ahcs_missing_value_raises(self):
        # Variant selected but a group carries no AHCS measurement:
        # ERROR logged, run fails (no silent fallback).
        from pblca.registry import AnimalGroup, FarmContext, ModelContext
        from pblca.processes.enteric import _make_enteric_measured

        ps = build_default_parameter_set()
        log = DiagLogger()
        animal = AnimalGroup(
            key="g", n_head=1, bw_start=200, bw_end=350, days=182,
            diet_de=0.65, diet_ge_density=18.45,
        )
        farm = FarmContext(
            farm_id="t", animals=[animal], parcels=[], purchases={},
            manure_split={},
        )
        ctx = ModelContext(farm, ps, ps.central_values(), log)
        with pytest.raises(ValueError):
            _make_enteric_measured("ahcs")(ctx)
        assert log.n_errors == 1

    def test_measured_ahcs_registered_variant(self, engine, farm):
        # The variant is registered and runs end-to-end through the
        # engine, with its bibliographic reference in the ledger.
        for a in farm.animals:
            a.ch4_measured_ahcs = 250.0
            a.ch4_measured_ahcs_rel_sd = 0.08
        r = engine.run(
            farm,
            model_selection={"enteric_ch4": "measured_ahcs"},
            record=False,
        )
        expected = sum(
            250.0 / 1000.0 * a.days * a.n_head for a in farm.animals
        )
        assert r.ledger.total("CH4") > 0
        enteric_entries = [
            e for e in r.ledger.entries()
            if e.source == "enteric" and e.model == "measured_ahcs"
        ]
        assert enteric_entries
        assert any("Zimmerman" in e.reference for e in enteric_entries)
        # Manure CH4 still computed (ration mode unchanged).
        assert any(
            e.source == "manure_ch4" for e in r.ledger.entries()
        )

    def test_measured_ahcs_mc_uncertainty(self, engine, farm):
        # The AHCS measurement uncertainty propagates through the MC
        # and the run is reproducible for a given seed.
        for a in farm.animals:
            a.ch4_measured_ahcs = 250.0
            a.ch4_measured_ahcs_rel_sd = 0.10
        mc1 = engine.run_monte_carlo(
            farm, n_iterations=40, seed=29,
            model_selection={"enteric_ch4": "measured_ahcs"},
            record=False,
        )
        mc2 = engine.run_monte_carlo(
            farm, n_iterations=40, seed=29,
            model_selection={"enteric_ch4": "measured_ahcs"},
            record=False,
        )
        s1 = mc1["impacts"]["gwp100"]
        s2 = mc2["impacts"]["gwp100"]
        assert s1["mean"] == pytest.approx(s2["mean"])
        assert s1["sd"] > 0
        assert s1["n"] == 40
        # Idempotence: measured values restored.
        assert all(a.ch4_measured_ahcs == 250.0 for a in farm.animals)

    def test_tier3_sauvant2011_central_value(self, engine):
        # Hand-checked central value of the INRA Tier-3 enteric equation
        # (Sauvant et al. 2011, eq. [9]) on the first case-study group.
        from pblca.processes.enteric import _energy_chain
        from pblca.registry import ModelContext

        farm = build_case_study_farm(engine.params)
        for a in farm.animals:  # DEMO diet characterisation (INRA 2018)
            a.diet_om, a.diet_omd = 0.91, 0.72
        ps = build_default_parameter_set()
        log = DiagLogger()
        ctx = ModelContext(farm, ps, ps.central_values(), log)
        g = farm.animals[0]
        e = _energy_chain(ctx, g)
        dmi, bw = e["dmi_kg_day"], e["bw_avg"]
        na = 100.0 * dmi / bw
        pco = g.share_concentrate
        domi = dmi * g.diet_om * g.diet_omd
        v = ps.central_values()
        ch4_g_kg_modi = (
            v["t3_sauv_a0"] + v["t3_sauv_a1"] * na + v["t3_sauv_a2"] * na**2
            + v["t3_sauv_b1"] * pco + v["t3_sauv_b2"] * pco**2
            + v["t3_sauv_b3"] * na * pco
        )
        expected = ch4_g_kg_modi * domi * g.days * g.n_head / 1000.0
        # Exact per-group check via the model function trace.
        from pblca.processes.enteric import enteric_tier3_sauvant2011
        res = enteric_tier3_sauvant2011(ctx)
        assert res.trace["per_group"][g.key]["ch4_kg"] == pytest.approx(expected)
        assert res.model_name == "tier3_sauvant2011"

    def test_tier3_sauvant2011_missing_diet_fields_raises(self):
        # The INRA Tier-3 variant requires explicit diet_om/diet_omd:
        # ERROR logged, no silent fallback.
        from pblca.registry import AnimalGroup

        animal = AnimalGroup(
            key="g", n_head=1, bw_start=200, bw_end=350, days=182,
            diet_de=0.65, diet_ge_density=18.45,
        )
        farm2 = FarmContext(
            farm_id="t", animals=[animal], parcels=[], purchases={},
            manure_split={},
        )
        ps = build_default_parameter_set()
        log = DiagLogger()
        ctx = ModelContext(farm2, ps, ps.central_values(), log)
        from pblca.processes.enteric import enteric_tier3_sauvant2011
        with pytest.raises(ValueError):
            enteric_tier3_sauvant2011(ctx)
        assert log.n_errors == 1

    def test_tier3_sauvant2011_domain_warnings(self):
        # Feeding level outside the Rumener calibration domain and
        # very high concentrate share: WARNINGs are logged.
        from pblca.registry import AnimalGroup

        animal = AnimalGroup(
            key="g", n_head=1, bw_start=400, bw_end=450, days=100,
            diet_de=0.70, diet_ge_density=18.45,
            dmi_measured=2.0,  # NA ≈ 0.5 % BW: below the Rumener domain
            diet_om=0.91, diet_omd=0.72,
            share_concentrate=0.70,
        )
        farm2 = FarmContext(
            farm_id="t", animals=[animal], parcels=[], purchases={},
            manure_split={},
        )
        ps = build_default_parameter_set()
        log = DiagLogger()
        ctx = ModelContext(farm2, ps, ps.central_values(), log)
        from pblca.processes.enteric import enteric_tier3_sauvant2011
        enteric_tier3_sauvant2011(ctx)
        msgs = [m["message"] for m in log.as_list() if m["level"] == "WARNING"]
        assert any("feeding level" in m.lower() or "NA=" in m for m in msgs)
        assert any("concentrate" in m.lower() for m in msgs)

    def test_manure_tier3_eugene2019_vs_tier2(self, engine):
        # Manure Tier-3: VS = non-digestible OM (DMI × diet_om × (1-omd))
        # instead of the IPCC Eq. 10.24 pathway; both variants run and
        # differ, and the Tier-3 value is hand-checkable.
        from pblca.processes.manure import manure_ch4_eugene2019
        from pblca.processes.enteric import _energy_chain

        farm = build_case_study_farm(engine.params)
        for a in farm.animals:  # DEMO diet characterisation (INRA 2018)
            a.diet_om, a.diet_omd = 0.91, 0.72
        ps = build_default_parameter_set()
        log = DiagLogger()
        ctx = ModelContext(farm, ps, ps.central_values(), log)
        res = manure_ch4_eugene2019(ctx)
        assert res.model_name == "tier3_eugene2019"
        assert res.ch4_kg > 0
        # Hand check: sum over all groups of
        # DMI × diet_om × (1-omd) × 365 × B0 × Σ MCF×share × n_head.
        v = ps.central_values()
        mcf_sum = sum(
            (v["mcf_prp"] if sys_ == "pasture" else v["mcf_solid_storage"])
            * s for sys_, s in farm.manure_split.items()
        )
        expected = 0.0
        for g in farm.animals:
            e = _energy_chain(ctx, g)
            vs_day = e["dmi_kg_day"] * g.diet_om * (1.0 - g.diet_omd)
            expected += (
                vs_day * 365.0 * v["bo_cattle_manure"] * mcf_sum * g.n_head
            )
        assert res.ch4_kg == pytest.approx(expected, rel=1e-9)
        # VS flux traced.
        assert res.fluxes["vs_total_kg"] == pytest.approx(
            sum(
                _energy_chain(ctx, g)["dmi_kg_day"]
                * g.diet_om * (1.0 - g.diet_omd) * 365.0 * g.n_head
                for g in farm.animals
            ), rel=1e-9,
        )
        # Registered and selectable through the engine.
        r3 = engine.run(
            farm, model_selection={"manure_ch4": "tier3_eugene2019"},
            record=False,
        )
        r2 = engine.run(
            farm, model_selection={"manure_ch4": "ipcc_tier2"}, record=False
        )
        assert r3.impacts["gwp100"] != r2.impacts["gwp100"]

    def test_manure_tier3_missing_diet_fields_raises(self):
        from pblca.registry import AnimalGroup

        animal = AnimalGroup(
            key="g", n_head=1, bw_start=200, bw_end=350, days=182,
            diet_de=0.65, diet_ge_density=18.45,
        )
        farm2 = FarmContext(
            farm_id="t", animals=[animal], parcels=[], purchases={},
            manure_split={"solid_storage": 1.0},
        )
        ps = build_default_parameter_set()
        log = DiagLogger()
        ctx = ModelContext(farm2, ps, ps.central_values(), log)
        from pblca.processes.manure import manure_ch4_eugene2019
        with pytest.raises(ValueError):
            manure_ch4_eugene2019(ctx)
        assert log.n_errors == 1

    def test_tier3_inra_variants_registered_and_mc_reproducible(self, engine, farm_inra_tier3):
        farm = farm_inra_tier3
        # Both INRA Tier-3 variants are registered, run together, and
        # the Monte-Carlo is reproducible for a given seed.
        sel = {
            "enteric_ch4": "tier3_sauvant2011",
            "manure_ch4": "tier3_eugene2019",
        }
        mc1 = engine.run_monte_carlo(
            farm, n_iterations=30, seed=7, model_selection=sel, record=False
        )
        mc2 = engine.run_monte_carlo(
            farm, n_iterations=30, seed=7, model_selection=sel, record=False
        )
        assert mc1["impacts"]["gwp100"]["mean"] == pytest.approx(
            mc2["impacts"]["gwp100"]["mean"]
        )
        assert mc1["impacts"]["gwp100"]["sd"] > 0
        assert mc1["impacts"]["gwp100"]["n"] == 30

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
        r2 = engine.run(farm, model_selection={"enteric_ch4": "tier2_2006"}, record=False)
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

    def test_model_outputs_preserved(self, engine, farm_inra_tier3):
        """The intermediate computations of every model (per-group
        DMI, Ym, DOMI, VS per system, ...) are preserved on the
        SimulationResult and written to the JSON datastore."""
        farm = farm_inra_tier3
        r = engine.run(
            farm,
            model_selection={"enteric_ch4": "tier3_sauvant2011"},
            sim_id="with_outputs",
            record=True,
        )
        out = r.model_outputs["enteric_ch4"]
        assert out["variant"] == "tier3_sauvant2011"
        g0 = farm.animals[0]
        tg = out["trace"]["per_group"][g0.key]
        assert tg["dmi_kg_day"] > 0
        assert "ch4_g_kg_modi" in tg
        # Every slot is present.
        for slot in (
            "enteric_ch4", "manure_ch4", "manure_n2o",
            "soil_n2o", "soil_carbon", "purchases", "fieldwork",
        ):
            assert slot in r.model_outputs
        # JSON entry carries the model_outputs.
        entry = [
            e for e in engine.datastore._entries
            if e["sim_id"] == "with_outputs"
        ][0]
        assert "model_outputs" in entry
        assert (
            entry["model_outputs"]["enteric_ch4"]["trace"]["per_group"][
                g0.key
            ]["ch4_kg"]
            == pytest.approx(tg["ch4_kg"])
        )

    def test_monte_carlo_return_traces(self, engine, farm):
        """return_traces=True gives the per-iteration model outputs
        for post-hoc analysis; nothing extra is written to JSON."""
        mc = engine.run_monte_carlo(
            farm, n_iterations=8, seed=5, record=False, return_traces=True,
        )
        assert len(mc["iteration_outputs"]) == 8
        it0 = mc["iteration_outputs"][0]["enteric_ch4"]
        assert "trace" in it0 and "variant" in it0
        mc2 = engine.run_monte_carlo(
            farm, n_iterations=8, seed=5, record=False,
        )
        assert "iteration_outputs" not in mc2

    def test_monte_carlo_per_group_ch4_stats(self, engine, farm):
        """MC uncertainty includes per-animal-group enteric CH4 stats:
        central_kg (unperturbed run) plus {mean, sd, p5, p50, p95, n}.
        The sum of the group central values matches the enteric CH4
        recorded in the central ledger exactly, and the JSON entry
        carries the same section."""
        mc = engine.run_monte_carlo(
            farm, n_iterations=15, seed=3, record=True,
        )
        groups = mc["enteric_ch4_per_group_kg"]
        assert set(groups) == {
            "veaux_0_6mois", "jeunes_6_12mois", "engraissés_12_21mois",
        }
        for stats in groups.values():
            assert stats["n"] == 15
            for key in ("central_kg", "mean", "sd", "p5", "p50", "p95"):
                assert key in stats and stats[key] > 0
            assert stats["p5"] <= stats["p50"] <= stats["p95"]
        # Central values: exact match with the central run's enteric CH4.
        central = engine.run(farm, sim_id="central_check", record=False)
        enteric_kg = sum(
            e.amount_kg for e in central.ledger.entries()
            if e.gas == "CH4" and e.source == "enteric"
        )
        assert sum(s["central_kg"] for s in groups.values()) == pytest.approx(
            enteric_kg
        )
        # Monte-Carlo means stay close to the central values (same
        # distributions, 15 iterations).
        assert sum(s["mean"] for s in groups.values()) == pytest.approx(
            enteric_kg, rel=0.1
        )
        # The JSON entry carries the same per-group section.
        entry = [
            e for e in engine.datastore._entries
            if "uncertainty" in e
            and "enteric_ch4_per_group_kg" in e["uncertainty"]
        ][0]
        assert entry["uncertainty"]["enteric_ch4_per_group_kg"] == groups
        # Section is JSON-serializable.
        json.dumps(groups)

    def test_monte_carlo_per_group_stats_meas_ahcs(self, engine, farm):
        """Per-group CH4 stats also work with a measured variant:
        the AHCS relative uncertainty (ch4_measured_ahcs_rel_sd)
        propagates through the Monte-Carlo even when no measured
        ration (dmi/ge) is set on the groups."""
        ahcs = {  # DEMO
            "veaux_0_6mois": 90.0,
            "jeunes_6_12mois": 180.0,
            "engraissés_12_21mois": 260.0,
        }
        by_key = {a.key: a for a in farm.animals}
        for a in farm.animals:
            a.ch4_measured_ahcs = ahcs[a.key]
            a.ch4_measured_ahcs_rel_sd = 0.08
        mc = engine.run_monte_carlo(
            farm, n_iterations=30, seed=7,
            model_selection={"enteric_ch4": "measured_ahcs"},
            record=False,
        )
        groups = mc["enteric_ch4_per_group_kg"]
        assert set(groups) == set(ahcs)
        # Central values: value/1000 * days * n_head.
        for k, stats in groups.items():
            g = by_key[k]
            assert stats["central_kg"] == pytest.approx(
                ahcs[k] / 1000.0 * g.days * g.n_head
            )
            # 8 % lognormal factor on the measurement -> sd > 0.
            assert stats["sd"] > 0
        # Idempotence: measured values restored by the MC.
        for a in farm.animals:
            assert a.ch4_measured_ahcs == ahcs[a.key]
            assert a.ch4_measured_ahcs_rel_sd == 0.08


class TestScenarioGrid:
    """Declarative case-study configurations and the scenario grid."""

    def test_grid_runs_all_variants_and_records(self, engine):
        from pblca.scenarios import (
            CaseStudyConfig,
            GroupMeasurements,
            run_scenario_grid,
        )

        config = CaseStudyConfig(
            name="test_farm",
            farm_builder=build_case_study_farm,
            measurements=GroupMeasurements(
                ch4_ahcs={
                    "veaux_0_6mois": 90.0,
                    "jeunes_6_12mois": 180.0,
                    "engraiss\u00e9s_12_21mois": 260.0,
                },
                ch4_ahcs_rel_sd=0.08,
                diet_om={
                    "veaux_0_6mois": 0.91,
                    "jeunes_6_12mois": 0.91,
                    "engraiss\u00e9s_12_21mois": 0.91,
                },
                diet_omd={
                    "veaux_0_6mois": 0.72,
                    "jeunes_6_12mois": 0.67,
                    "engraiss\u00e9s_12_21mois": 0.64,
                },
            ),
            variant_grid={
                "enteric_ch4": ["tier2_2006", "measured_ahcs"],
            },
            named_combinations={
                "inra": {
                    "enteric_ch4": "tier2_2006",
                    "manure_ch4": "ipcc_tier2",
                },
            },
        )
        records = run_scenario_grid(engine, config, record=True)
        assert len(records) == 3
        assert all(not r.excluded for r in records)
        ids = {r.sim_id for r in records}
        assert "grid_test_farm_inra" in ids
        assert "grid_test_farm_enteric_ch4=tier2_2006" in ids
        # Each scenario recorded one JSON entry with its selection.
        for r in records:
            entry = [
                e for e in engine.datastore._entries
                if e["sim_id"] == r.sim_id
            ][0]
            assert entry["model_selection"]["enteric_ch4"]["variant"] == (
                r.model_selection["enteric_ch4"]
            )

    def test_grid_excludes_variants_with_missing_measurements(self, engine):
        # No measurement on the farm: measured_ahcs and the INRA
        # Tier-3 variants are excluded with a reason, not a crash.
        from pblca.scenarios import CaseStudyConfig, run_scenario_grid

        config = CaseStudyConfig(
            name="no_data_farm",
            farm_builder=build_case_study_farm,
            variant_grid={
                "enteric_ch4": ["tier2_2006", "measured_ahcs",
                                "tier3_sauvant2011"],
            },
        )
        records = run_scenario_grid(engine, config, record=True)
        by_variant = {
            r.model_selection["enteric_ch4"]: r for r in records
        }
        ok = by_variant["tier2_2006"]
        assert not ok.excluded
        for variant in ("measured_ahcs", "tier3_sauvant2011"):
            r = by_variant[variant]
            assert r.excluded
            assert "missing" in r.reason
        # Excluded scenarios are NOT recorded in the datastore.
        assert not any(
            e["sim_id"].endswith(f"={v}")
            for e in engine.datastore._entries
            for v in ("measured_ahcs", "tier3_sauvant2011")
        )

    def test_run_case_study_orchestrates_grid_mc_and_comparison(self, tmp_path):
        from pblca.engine import LCAEngine
        from pblca.scenarios import (
            CaseStudyConfig,
            GroupMeasurements,
            NumericalOptions,
            run_case_study,
        )

        engine = LCAEngine(datastore_path=str(tmp_path / "results.json"))
        config = CaseStudyConfig(
            name="orch_farm",
            farm_builder=build_case_study_farm,
            measurements=GroupMeasurements(
                ch4_ahcs={
                    "veaux_0_6mois": 90.0, "jeunes_6_12mois": 180.0,
                    "engraissés_12_21mois": 260.0,
                },
                ch4_ahcs_rel_sd=0.08,
                dmi_measured={
                    "veaux_0_6mois": 4.2, "jeunes_6_12mois": 7.4,
                    "engraissés_12_21mois": 10.2,
                },
                ration_rel_sd=0.10,
                diet_om={"veaux_0_6mois": 0.91},
                diet_omd={"veaux_0_6mois": 0.72},
            ),
            variant_grid={"enteric_ch4": ["tier2_2006"]},
            mc=NumericalOptions(n_iterations=10, seed=3),
        )
        summary = run_case_study(engine, config, record=True)
        # Grid: one scenario, recorded.
        assert len(summary["scenarios"]) == 1
        assert not summary["scenarios"][0]["excluded"]
        # Monte-Carlo per enteric variant of the grid, per-group CH4
        # section present in the JSON entry.
        assert "monte_carlo" in summary
        mc_entry = [
            e for e in engine.datastore._entries
            if e["sim_id"] == "mc_orch_farm_enteric_tier2_2006"
        ][0]
        assert "enteric_ch4_per_group_kg" in mc_entry["uncertainty"]
        # Paired ration comparison ran (measured rations available).
        assert "ration_comparison" in summary
        assert not isinstance(summary["ration_comparison"], str)


class TestIngestionExplicitVariants:
    """Ingestion-explicit enteric variants (modelled vs measured)."""

    def test_variant_names_and_requirements(self):
        reg = build_default_registry()
        variants = reg.variants("enteric_ch4")
        for base in ("tier2_2006", "tier2_2019", "tier2_fao_ym",
                     "tier3_mills", "tier3_sauvant2011"):
            assert f"{base}_modelled_ingestion" in variants
            assert f"{base}_ingestion_measured" in variants
            # Measured version requires dmi_measured (+ INRA fields
            # for Sauvant).
            spec_m = reg.get("enteric_ch4", f"{base}_ingestion_measured")
            assert "dmi_measured" in spec_m.required_group_fields
        # measured_ahcs is not doubled (bypasses the ration chain).
        assert "measured_ahcs_modelled_ingestion" not in variants

    def test_measured_variant_uses_dmi_and_derives_ge(self, engine):
        farm = build_case_study_farm(engine.params)
        dmi = {"veaux_0_6mois": 4.2, "jeunes_6_12mois": 7.4,
               "engraissés_12_21mois": 10.2}
        density = 18.45
        for a in farm.animals:
            a.dmi_measured = dmi[a.key]
            a.ge_measured = None
        r = engine.run(
            farm,
            model_selection={"enteric_ch4": "tier2_2006_ingestion_measured"},
            record=False,
        )
        tr = r.model_outputs["enteric_ch4"]["trace"]["per_group"]
        for key, value in dmi.items():
            assert tr[key]["dmi_kg_day"] == pytest.approx(value)
            assert tr[key]["ge_mj_day"] == pytest.approx(value * density)

    def test_modelled_variant_ignores_measured_intakes(self, engine):
        farm = build_case_study_farm(engine.params)
        for a in farm.animals:
            a.dmi_measured = 99.9  # absurd value, must be ignored
        r_mod = engine.run(
            farm,
            model_selection={"enteric_ch4": "tier2_2006_modelled_ingestion"},
            record=False,
        )
        r_bare = engine.run(
            farm,
            model_selection={"enteric_ch4": "tier2_2006"},
            record=False,
        )
        # Bare name = modelled_ingestion alias: identical emissions.
        assert sum(
            e.amount_kg for e in r_mod.ledger.entries()
            if e.source == "enteric"
        ) == pytest.approx(sum(
            e.amount_kg for e in r_bare.ledger.entries()
            if e.source == "enteric"
        ))
        # The absurd measured DMI did not leak into the trace.
        tr = r_mod.model_outputs["enteric_ch4"]["trace"]["per_group"]
        assert all(t["dmi_kg_day"] < 30 for t in tr.values())
        # Ignoring warnings were logged (one per group).
        warns = [m for m in r_mod.diagnostics
                 if m["level"] == "WARNING" and "IGNORED" in m["message"]]
        assert len(warns) == len(farm.animals)

    def test_measured_variant_excluded_without_dmi(self, engine):
        # No measurement: the grid excludes the ingestion_measured
        # variants automatically.
        from pblca.scenarios import CaseStudyConfig, run_scenario_grid

        config = CaseStudyConfig(
            name="nodmi_farm",
            farm_builder=build_case_study_farm,
            variant_grid={
                "enteric_ch4": [
                    "tier2_2006_modelled_ingestion",
                    "tier2_2006_ingestion_measured",
                ],
            },
        )
        records = run_scenario_grid(engine, config, record=True)
        by_variant = {r.model_selection["enteric_ch4"]: r for r in records}
        assert not by_variant["tier2_2006_modelled_ingestion"].excluded
        assert by_variant["tier2_2006_ingestion_measured"].excluded
        assert "dmi_measured" in by_variant[
            "tier2_2006_ingestion_measured"
        ].reason

    def test_mc_per_group_stats_ingestion_measured(self, engine):
        farm = build_case_study_farm(engine.params)
        dmi = {"veaux_0_6mois": 4.2, "jeunes_6_12mois": 7.4,
               "engraissés_12_21mois": 10.2}
        for a in farm.animals:
            a.dmi_measured = dmi[a.key]
            a.ration_rel_sd = 0.10
        mc = engine.run_monte_carlo(
            farm, n_iterations=20, seed=11,
            model_selection={"enteric_ch4": "tier2_2006_ingestion_measured"},
            record=False,
        )
        groups = mc["enteric_ch4_per_group_kg"]
        assert set(groups) == set(dmi)
        for stats in groups.values():
            assert stats["sd"] > 0  # ration_rel_sd propagates
        mc_bare = engine.run_monte_carlo(
            farm, n_iterations=20, seed=11,
            model_selection={"enteric_ch4": "tier2_2006_modelled_ingestion"},
            record=False,
        )
        sd_mod = mc_bare["gas_totals_kg"]["CH4"]["sd"]
        # measured rations add their quantification error -> wider sd
        # than the modelled chain (which ignores them).
        assert groups["veaux_0_6mois"]["sd"] > 0
        assert mc["gas_totals_kg"]["CH4"]["sd"] > sd_mod
