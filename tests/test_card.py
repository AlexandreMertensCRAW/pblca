"""Tests of the declarative study card (TOML) and its CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pblca.card import CardError, load_card, run_card
from pblca.scenarios import NumericalOptions

CARDS_DIR = Path(__file__).resolve().parents[1] / "cards"

EXAMPLE_CARD = CARDS_DIR / "studies" / "ferme_20ha.toml"

MINIMAL_CARD = """
[study]
name = "mini"
farm = "ferme_20ha"
"""


def _write_card(tmp_path, text):
    path = tmp_path / "card.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_load_example_card():
    card = load_card(str(EXAMPLE_CARD))
    assert card.name == "ferme_20ha"
    assert card.farm.farm_id == "ferme_cas_etude_20ha"
    assert card.mc == NumericalOptions(n_iterations=500, seed=2024)
    assert card.model_selection["enteric_ch4"] == "tier2_2006_modelled_ingestion"
    assert len(card.variant_grid["enteric_ch4"]) == 11
    assert card.main_enteric_variant == "tier2_fao_ym_modelled_ingestion"
    assert "inra_tier3" in card.named_combinations
    assert card.source_sha256 != ""


def test_unknown_slot_fails_fast():
    text = MINIMAL_CARD + "\n[model_selection]\nnope = \"x\"\n"
    with pytest.raises(CardError, match="unknown slot 'nope'"):
        load_card(_write_card_tmp(text))


def _write_card_tmp(text):
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
        f.write(text)
        return f.name


def test_unknown_variant_fails_fast():
    text = MINIMAL_CARD + '\n[model_selection]\nenteric_ch4 = "not_a_variant"\n'
    with pytest.raises(CardError, match="Unknown variant"):
        load_card(_write_card_tmp(text))


def test_unknown_builtin_farm_fails_fast():
    with pytest.raises(CardError, match="unknown farm"):
        load_card(_write_card_tmp('[study]\nfarm = "nope"\n'))


def test_unknown_key_rejected():
    text = MINIMAL_CARD.replace('name = "mini"', 'name = "mini"\nwhatever = 1')
    with pytest.raises(CardError, match="unknown key"):
        load_card(_write_card_tmp(text))


def test_inline_farm_card():
    text = """
[study]
name = "inline"

[farm]
farm_id = "ferme_test"
avg_temp = 10.0

[farm.manure_split]
pasture = 1.0

[farm.purchases]
concentrate_kg_dm = 1000.0

[[farm.animals]]
key = "veaux"
n_head = 10.0
days = 183
bw_start = 50.0
bw_end = 200.0
diet_de = 0.70
grazing = 1.0

[[farm.parcels]]
key = "prairie"
crop = "prairie_permanente"
area = 5.0
"""
    card = load_card(_write_card_tmp(text))
    assert card.farm.farm_id == "ferme_test"
    assert card.farm.animals[0].key == "veaux"
    assert card.farm.parcels[0].area == 5.0


def test_run_card_end_to_end(tmp_path):
    text = (
        MINIMAL_CARD
        + """
[variant_grid]
enteric_ch4 = ["tier2_2006_modelled_ingestion", "tier2_2019_modelled_ingestion"]

[monte_carlo]
n_iterations = 5
seed = 2024

[datastore]
path = "%s"
"""
        % (tmp_path / "out.json")
    )
    card = load_card(_write_card_tmp(text))
    summary = run_card(card)
    assert summary["case_study"] == "mini"
    out = tmp_path / "out.json"
    assert out.exists()
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["card"]["name"] == "mini"
    assert doc["card"]["sha256"] == card.source_sha256
    assert doc["card_source"]
    assert doc["simulations"]


def test_cli_slots():
    result = subprocess.run(
        [sys.executable, "-m", "pblca.cli", "slots"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "enteric_ch4" in result.stdout
    assert "tier2_2006_modelled_ingestion" in result.stdout


def test_cli_run_invalid_card(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("[study]\nfarm = \"nope\"\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pblca.cli", "run", str(bad)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "unknown farm" in result.stderr
