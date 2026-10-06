"""Contract tests: JSON schema consumed by the R analysis scripts.

The R scripts (``R/plot_*.R``) read ``results.json`` and expect a fixed
set of keys. That contract is implicit: nothing in the Python code or
the pytest suite would fail if a key were renamed — the R figures
would break silently (empty selection -> ``stop()``). These tests make
the contract explicit: a full case study is run, the JSON document is
reloaded from disk, and every key path actually dereferenced by the R
scripts is asserted to exist with the expected structure.
"""

import json
import os

import pytest

from pblca import LCAEngine
from pblca.case_study import build_case_study_farm
from pblca.scenarios import (
    CaseStudyConfig,
    GroupMeasurements,
    NumericalOptions,
    run_case_study,
)


def _dereference(entry: dict, path: str) -> object:
    """Walk a dotted path in a JSON entry (``a.b.c``), raising
    ``KeyError`` with the full path if a key is missing."""
    node: object = entry
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"missing key 'uncertainty.{path}' in entry "
                           f"'{entry.get('sim_id', '?')}'")
        node = node[part]
    return node


def _full_results(tmp_path):
    """Run a case study with a minimal but complete grid (one
    Monte-Carlo per swept variant + the paired ration comparison)
    and return the JSON document reloaded from disk."""
    results_path = str(tmp_path / "results.json")
    engine = LCAEngine(datastore_path=results_path)
    farm = build_case_study_farm(engine.params)
    config = CaseStudyConfig(
        name="contract",
        farm_builder=lambda params: farm,
        measurements=GroupMeasurements(
            ch4_ahcs={"veaux_0_6mois": 90.0},
            ch4_ahcs_rel_sd=0.08,
            dmi_measured={
                "veaux_0_6mois": 4.2,
                "jeunes_6_12mois": 7.4,
                "engraissés_12_21mois": 10.2,
            },
            ration_rel_sd=0.10,
        ),
        variant_grid={
            "enteric_ch4": [
                "tier2_2006_modelled_ingestion",
                "tier2_2006_ingestion_measured",
                "measured_ahcs",
            ],
            "manure_ch4": ["ipcc_tier2", "tier3_eugene2019"],
        },
        mc=NumericalOptions(n_iterations=10, seed=2024),
    )
    run_case_study(engine, config, record=True)
    engine.datastore.save()
    with open(results_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    return _full_results(tmp_path_factory.mktemp("r_contract"))


@pytest.fixture(scope="module")
def mc_entry(results):
    """A Monte-Carlo entry (uncertainty per enteric variant)."""
    entries = [
        e for e in results["simulations"]
        if "uncertainty" in e
        and "enteric_ch4_per_group_kg" in e["uncertainty"]
    ]
    assert entries, "no Monte-Carlo entry in results.json"
    return entries[0]


@pytest.fixture(scope="module")
def ration_entry(results):
    """The ration-comparison entry (paired Monte-Carlo)."""
    entries = [
        e for e in results["simulations"]
        if "uncertainty" in e
        and "enteric_ch4_samples" in e["uncertainty"]
    ]
    assert entries, "no ration-comparison entry in results.json"
    return entries[0]


class TestDocumentStructure:
    """Top-level structure read by every R script (fromJSON)."""

    def test_format_version(self, results):
        assert results["format_version"] == "1.0"

    def test_simulations_list(self, results):
        assert isinstance(results["simulations"], list)
        assert len(results["simulations"]) > 0

    def test_entry_core_fields(self, results):
        for e in results["simulations"]:
            for key in ("sim_id", "timestamp", "farms", "model_selection"):
                assert key in e, f"'{key}' missing in entry {e.get('sim_id')}"


class TestMonteCarloContract:
    """Keys read by plot_enteric_ch4_groups.R, plot_enteric_ch4_by_model.R,
    plot_enteric_ch4_per_head_day.R and plot_manure_ch4_by_model.R."""

    # model_selection$enteric_ch4$variant (variant filtering per plot)
    def test_model_selection_enteric(self, mc_entry):
        sel = mc_entry["model_selection"]["enteric_ch4"]
        assert isinstance(sel, dict) and "variant" in sel

    def test_model_selection_manure(self, mc_entry):
        sel = mc_entry["model_selection"]["manure_ch4"]
        assert isinstance(sel, dict) and "variant" in sel

    # uncertainty$n_iterations, $seed (plot subtitles)
    def test_mc_meta(self, mc_entry):
        for key in ("n_iterations", "seed", "failed_iterations", "method"):
            assert key in mc_entry["uncertainty"]

    # uncertainty$enteric_ch4_per_group_kg: per group key, the R scripts
    # dereference $central_kg, $mean, $sd, $p5, $p50, $p95, $n.
    def test_per_group_stats_fields(self, mc_entry):
        groups = mc_entry["uncertainty"]["enteric_ch4_per_group_kg"]
        assert len(groups) > 0
        for group_key, g in groups.items():
            for field in ("central_kg", "mean", "sd", "p5", "p50", "p95", "n"):
                assert field in g, (
                    f"'{field}' missing for group '{group_key}' "
                    "in enteric_ch4_per_group_kg"
                )

    # uncertainty$enteric_ch4_per_group_g_day (plot_enteric_ch4_per_head_day.R)
    def test_per_head_day_stats_fields(self, mc_entry):
        groups = mc_entry["uncertainty"]["enteric_ch4_per_group_g_day"]
        assert len(groups) > 0
        for group_key, g in groups.items():
            for field in ("central_g_day", "mean", "sd", "p5", "p50", "p95", "n"):
                assert field in g, (
                    f"'{field}' missing for group '{group_key}' "
                    "in enteric_ch4_per_group_g_day"
                )

    # uncertainty$manure_ch4_by_system_kg (plot_manure_ch4_by_model.R)
    def test_manure_by_system_fields(self, mc_entry):
        systems = mc_entry["uncertainty"]["manure_ch4_by_system_kg"]
        assert len(systems) > 0
        for system_key, s in systems.items():
            for field in ("central_kg", "mean", "sd", "p5", "p50", "p95", "n"):
                assert field in s, (
                    f"'{field}' missing for system '{system_key}' "
                    "in manure_ch4_by_system_kg"
                )

    # sim_id convention: mc_<case>_enteric_ch4_<variant> — the by-model
    # plots group the entries by parsing the sim_id suffix.
    def test_mc_sim_id_convention(self, results):
        mc_ids = [
            e["sim_id"] for e in results["simulations"]
            if "uncertainty" in e
            and "enteric_ch4_per_group_kg" in e["uncertainty"]
        ]
        assert all(i.startswith("mc_") for i in mc_ids), mc_ids


class TestRationComparisonContract:
    """Keys read by plot_ration_comparison_correlation.R."""

    # uncertainty$enteric_ch4_samples: $ipcc_equations and $measured,
    # each mapping group keys + "farm_total" to paired per-iteration
    # samples (equal-length vectors, one draw per iteration).
    def test_enteric_ch4_samples(self, ration_entry):
        samples = ration_entry["uncertainty"]["enteric_ch4_samples"]
        for mode in ("ipcc_equations", "measured"):
            assert mode in samples
            assert "farm_total" in samples[mode]
            assert len(samples[mode]["farm_total"]) > 0
        n_ipcc = len(samples["ipcc_equations"]["farm_total"])
        n_meas = len(samples["measured"]["farm_total"])
        assert n_ipcc == n_meas, "unpaired ration-comparison samples"


class TestPairedEntericGridContract:
    """Keys read by analyse_enteric_sensitivity.R."""

    # uncertainty$emissions_table: one row per iteration, carrying
    # "iteration" and one column per evaluated variant; every row has
    # the SAME set of keys (rectangular table, paired columns).
    def test_paired_grid_entry(self, results):
        entries = [
            e for e in results["simulations"]
            if "uncertainty" in e
            and "emissions_table" in e["uncertainty"]
        ]
        assert entries, "no paired-enteric-grid entry in results.json"
        entry = entries[-1]
        u = entry["uncertainty"]
        variants = u["variants"]
        main = u["main_variant"]
        assert main in variants
        assert isinstance(variants, list) and len(variants) >= 1
        assert len(u["emissions_table"]) == len(u["parameter_draws_table"])
        assert len(u["emissions_table"]) > 0
        indicators = ("gwp100", "gwp20", "gwpstar",
                      "ch4_kg", "co2_kg", "n2o_kg")
        expected_keys = {"iteration"}
        for v in variants:
            for k in indicators:
                expected_keys.add(f"{v}__{k}")
        # Source x gas columns of the main variant (engine section 3
        # of analyse_enteric_sensitivity.R reads them).
        expected_keys |= set(u["source_gas_columns"])
        for row in u["emissions_table"]:
            assert set(row) == expected_keys, "unpaired emissions row"
        # parameter_draws_table: same iterations, every pid present.
        for row in u["parameter_draws_table"]:
            assert "iteration" in row
        iterations_e = [r["iteration"] for r in u["emissions_table"]]
        iterations_p = [r["iteration"] for r in u["parameter_draws_table"]]
        assert iterations_e == iterations_p, "tables not paired"
        # sim_id written by run_case_study (step 4).
        assert entry["sim_id"] == "mc_contract_enteric_paired"

        # central_gwp_factors + source_gas_columns: read by section 3
        # of analyse_enteric_sensitivity.R (variance decomposition:
        # inventory vs characterisation split of the GWP100).
        for pid, value in u["central_gwp_factors"].items():
            assert pid.startswith(("gwp100_", "gwp20_"))
            assert isinstance(value, float)
        for col in u["source_gas_columns"]:
            assert col.count("__") == 1
            assert col.endswith(("_ch4_kg", "_n2o_kg", "_co2_kg"))
            for row in u["emissions_table"]:
                assert col in row, f"source column '{col}' missing"

    def test_paired_grid_stats(self, results):
        entries = [
            e for e in results["simulations"]
            if "uncertainty" in e
            and "farm_indicators_stats" in e["uncertainty"]
        ]
        assert entries
        u = entries[-1]["uncertainty"]
        variants = u["variants"]
        main = u["main_variant"]
        for k in ("gwp100", "gwp20", "gwpstar",
                  "ch4_kg", "co2_kg", "n2o_kg"):
            for v in variants:
                stats = u["farm_indicators_stats"][k][v]
                for key in ("mean", "sd", "p5", "p50", "p95", "n"):
                    assert key in stats
        # Paired differences: alternatives only, every indicator.
        alts = [v for v in variants if v != main]
        assert set(u["paired_differences_stats"]) == set(alts)
        for alt in alts:
            for k in ("gwp100", "gwp20", "gwpstar",
                      "ch4_kg", "co2_kg", "n2o_kg"):
                stats = u["paired_differences_stats"][alt][k]
                for key in ("mean", "sd", "p5", "p50", "p95", "n"):
                    assert key in stats


class TestDataStoreArchive:
    """Lifecycle: a previous results file is archived, not deleted."""

    def test_previous_file_archived(self, tmp_path):
        from pblca.engine import DataStore

        results_path = str(tmp_path / "results.json")
        store = DataStore(results_path)
        store.append({"sim_id": "old_run"})
        store.save()

        # A new run: the previous file must be moved to the archive.
        store2 = DataStore(results_path)
        assert len(store2) == 0
        assert not os.path.exists(results_path)
        archive_dir = tmp_path / "results_archive"
        archived = list(archive_dir.glob("results_*.json"))
        assert len(archived) == 1
        with open(archived[0], "r", encoding="utf-8") as f:
            doc = json.load(f)
        assert doc["simulations"][0]["sim_id"] == "old_run"

    def test_archive_previous_false_keeps_entries(self, tmp_path):
        from pblca.engine import DataStore

        results_path = str(tmp_path / "results.json")
        store = DataStore(results_path)
        store.append({"sim_id": "kept_run"})
        store.save()

        store2 = DataStore(results_path, archive_previous=False)
        assert len(store2) == 1

    def test_no_previous_file_no_archive(self, tmp_path):
        from pblca.engine import DataStore

        store = DataStore(str(tmp_path / "results.json"))
        assert len(store) == 0
        assert not (tmp_path / "results_archive").exists()
