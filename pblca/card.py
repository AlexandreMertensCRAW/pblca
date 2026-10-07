"""Declarative study card (TOML): one file = one reproducible study.

Inspired by the "card" pattern of collider fast-simulation frameworks
(e.g. Delphes): a study is fully described by a single declarative
file — the farm, the model variants to test, the Monte-Carlo plan and
the output datastore. No user code is required to run a study:

.. code-block:: console

    pblca run etude.toml

The card validates everything it can at load time (unknown slot,
unknown variant, missing farm description), so a typo fails fast
with an explicit message instead of a mid-simulation crash.

Card structure (all sections optional except the farm reference):

.. code-block:: toml

    [study]
    name = "ferme_20ha"

    # Either a built-in farm shipped with the package...
    farm = "ferme_20ha"
    # ...or a complete inline description (see [farm] below).

    [model_selection]
    enteric_ch4 = "tier2_2006_modelled_ingestion"

    [variant_grid]
    enteric_ch4 = ["tier2_2006_modelled_ingestion", "tier3_mills_modelled_ingestion"]

    [study.named_combinations]
    inra_tier3 = { enteric_ch4 = "tier3_sauvant2011_modelled_ingestion", manure_ch4 = "tier3_eugene2019" }

    [monte_carlo]
    n_iterations = 500
    seed = 2024

    [datastore]
    path = "results.json"

The inline farm mirrors :class:`pblca.farm_spec.FarmSpec`:

.. code-block:: toml

    [farm]
    farm_id = "ma_ferme"
    n_purchased = 60.0
    avg_temp = 11.0
    mature_weight = 700.0
    manure_exported = 0.0

    [farm.manure_split]
    pasture = 0.45
    solid_storage = 0.55

    [farm.purchases]
    concentrate_kg_dm = 15000.0

    [[farm.animals]]
    key = "veaux_0_6mois"
    days = 183
    bw_start = 50.0
    bw_end = 200.0
    diet_de = 0.70
    share_concentrate = 0.10
    grazing = 0.5
    system = "mixed"
    # on-farm measurements, on the group they belong to:
    dmi_measured = 4.2
    ch4_measured_ahcs = 90.0

    [[farm.parcels]]
    key = "prairie_permanente"
    crop = "prairie_permanente"
    area = 13.0
    is_grassland = true
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Dict, List, Optional, Union

from .farm_spec import AnimalGroupSpec, FarmSpec, ParcelSpec
from .params import ParameterSet
from .registry import ModelRegistry, build_default_registry
from .scenarios import CaseStudyConfig, NumericalOptions

# Built-in farms shipped with the package (pblca.case_study).
BUILTIN_FARMS: Dict[str, FarmSpec] = {}


def _register_builtins() -> None:
    from .case_study import FERME_20HA

    BUILTIN_FARMS["ferme_20ha"] = FERME_20HA


class CardError(ValueError):
    """Invalid card: unknown key, unknown slot/variant, missing farm."""


@dataclass
class Card:
    """Validated study card (loaded from a TOML file).

    Attributes:
        path: path of the TOML file the card was loaded from.
        name: study name (used in the JSON sim_id prefixes).
        farm: declarative farm specification (values only).
        model_selection: variant per slot for the central run
            (slots left out keep the registry default).
        variant_grid: variants to sweep per slot (each variant is
            simulated at central values and in Monte-Carlo).
        named_combinations: coherent scenarios run in addition to the
            one-slot sweeps ({name: {slot: variant}}).
        mc: Monte-Carlo options (None = no Monte-Carlo).
        datastore_path: output JSON file.
        source_sha256: SHA-256 of the card file (embedded in the
            results file for traceability, ISO 14044 §4.5).
        source_text: raw card text (embedded alongside the hash).
    """

    path: str
    name: str
    farm: FarmSpec
    model_selection: Dict[str, str] = field(default_factory=dict)
    variant_grid: Dict[str, List[str]] = field(default_factory=dict)
    named_combinations: Dict[str, Dict[str, str]] = field(default_factory=dict)
    mc: Optional[NumericalOptions] = None
    datastore_path: str = "results.json"
    source_sha256: str = ""
    source_text: str = ""

    def as_dict(self) -> Dict[str, Any]:
        """Card metadata stored in the results file (traceability)."""
        return {
            "path": self.path,
            "name": self.name,
            "sha256": self.source_sha256,
            "model_selection": dict(self.model_selection),
            "variant_grid": {k: list(v) for k, v in self.variant_grid.items()},
            "named_combinations": {
                k: dict(v) for k, v in self.named_combinations.items()
            },
            "monte_carlo": (
                None
                if self.mc is None
                else {"n_iterations": self.mc.n_iterations, "seed": self.mc.seed}
            ),
        }

    def to_case_study(self) -> CaseStudyConfig:
        """Translate the card into a CaseStudyConfig (orchestration
        input of :func:`pblca.scenarios.run_case_study`)."""
        return CaseStudyConfig(
            name=self.name,
            farm=self.farm,
            variant_grid=dict(self.variant_grid) or None,
            named_combinations=dict(self.named_combinations) or None,
            mc=self.mc,
        )


def _from_dataclass_dict(cls, data: Dict[str, Any], context: str):
    """Build a dataclass instance from a dict of TOML values, rejecting
    keys that do not match a field (typos fail fast with an explicit
    message)."""
    if not is_dataclass(cls):
        raise CardError(f"{context}: not a dataclass")
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise CardError(
            f"{context}: unknown key(s) {unknown} "
            f"(accepted: {sorted(known)})"
        )
    kwargs: Dict[str, Any] = {}
    for f in fields(cls):
        if f.name in data:
            kwargs[f.name] = data[f.name]
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise CardError(f"{context}: {exc}") from exc


def _parse_farm(data: Dict[str, Any]) -> FarmSpec:
    """Parse the inline [farm] section into a FarmSpec."""
    if "animals" not in data:
        raise CardError("[farm]: 'animals' is required (list of [[farm.animals]])")
    if "parcels" not in data:
        raise CardError("[farm]: 'parcels' is required (list of [[farm.parcels]])")
    animals_data = data.pop("animals")
    parcels_data = data.pop("parcels")
    purchases = data.pop("purchases", None)
    manure_split = data.pop("manure_split", None)
    if purchases is None:
        raise CardError("[farm]: 'purchases' is required ([farm.purchases])")
    if manure_split is None:
        raise CardError("[farm]: 'manure_split' is required ([farm.manure_split])")
    if not isinstance(animals_data, list) or not animals_data:
        raise CardError("[farm]: 'animals' must be a non-empty list")
    if not isinstance(parcels_data, list) or not parcels_data:
        raise CardError("[farm]: 'parcels' must be a non-empty list")
    animals = [
        _from_dataclass_dict(AnimalGroupSpec, a, f"[[farm.animals]] #{i}")
        for i, a in enumerate(animals_data)
    ]
    parcels = [
        _from_dataclass_dict(ParcelSpec, p, f"[[farm.parcels]] #{i}")
        for i, p in enumerate(parcels_data)
    ]
    base = _from_dataclass_dict(
        FarmSpec,
        dict(data, animals=[], parcels=[], purchases={}, manure_split={}),
        "[farm]",
    )
    return FarmSpec(
        farm_id=base.farm_id,
        animals=animals,
        parcels=parcels,
        purchases=dict(purchases),
        n_purchased=base.n_purchased,
        manure_split=dict(manure_split),
        manure_exported=base.manure_exported,
        avg_temp=base.avg_temp,
        mature_weight=base.mature_weight,
    )


def _validate_selection(
    registry: ModelRegistry, selection: Dict[str, str], context: str
) -> None:
    """Fail fast on unknown slot or unknown variant, listing what
    the registry actually offers."""
    for slot, variant in selection.items():
        if slot not in registry.slots():
            raise CardError(
                f"{context}: unknown slot '{slot}' "
                f"(available: {registry.slots()})"
            )
        try:
            registry.get(slot, variant)
        except KeyError as exc:
            available = [s.variant for s in registry.get_specs(slot)]
            raise CardError(
                f"{context}: {exc} (variants of '{slot}': {available})"
            ) from exc


def load_card(path: str, registry: Optional[ModelRegistry] = None) -> Card:
    """Load and validate a study card from a TOML file.

    Args:
        path: path of the TOML card.
        registry: model registry used to validate the slots and
            variants (default: the standard registry).

    Returns:
        a validated :class:`Card`.

    Raises:
        CardError: the card is structurally invalid or references an
            unknown slot / variant / built-in farm.
    """
    registry = registry or build_default_registry()
    with open(path, "rb") as f:
        source_text_bytes = f.read()
    try:
        doc = tomllib.loads(source_text_bytes.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CardError(f"{path}: invalid TOML: {exc}") from exc

    study = doc.get("study", {})
    if not isinstance(study, dict):
        raise CardError("'study' must be a table")
    unknown = sorted(set(study) - {"name", "farm", "named_combinations"})
    if unknown:
        raise CardError(f"[study]: unknown key(s) {unknown}")

    if not BUILTIN_FARMS:
        _register_builtins()

    farm_ref = study.get("farm")
    if farm_ref is None:
        farm_ref = doc.get("farm")
    if isinstance(farm_ref, str):
        if farm_ref not in BUILTIN_FARMS:
            raise CardError(
                f"[study]: unknown built-in farm '{farm_ref}' "
                f"(available: {sorted(BUILTIN_FARMS)})"
            )
        farm = BUILTIN_FARMS[farm_ref]
    elif isinstance(farm_ref, dict):
        farm = _parse_farm(dict(farm_ref))
    else:
        raise CardError(
            "[study]: 'farm' must be a built-in farm name (string) "
            "or an inline [farm] table"
        )

    model_selection = {
        k: v for k, v in doc.get("model_selection", {}).items()
        if isinstance(v, str)
    }
    _validate_selection(registry, model_selection, "[model_selection]")

    variant_grid = doc.get("variant_grid", {})
    if not isinstance(variant_grid, dict):
        raise CardError("'variant_grid' must be a table")
    for slot, variants in variant_grid.items():
        if not isinstance(variants, list) or not all(
            isinstance(v, str) for v in variants
        ):
            raise CardError(f"[variant_grid]: '{slot}' must be a list of strings")
        _validate_selection(registry, {slot: v for v in variants}, f"[variant_grid]")

    named_combinations: Dict[str, Dict[str, str]] = {}
    for name, combo in (study.get("named_combinations") or {}).items():
        if not isinstance(combo, dict) or not all(
            isinstance(v, str) for v in combo.values()
        ):
            raise CardError(
                f"[study.named_combinations]: '{name}' must be a table "
                "of {slot: variant}"
            )
        _validate_selection(
            registry, combo, f"[study.named_combinations] '{name}'"
        )
        named_combinations[name] = dict(combo)

    mc_data = doc.get("monte_carlo")
    mc: Optional[NumericalOptions] = None
    if mc_data is not None:
        if not isinstance(mc_data, dict):
            raise CardError("'monte_carlo' must be a table")
        unknown_mc = sorted(set(mc_data) - {"n_iterations", "seed"})
        if unknown_mc:
            raise CardError(f"[monte_carlo]: unknown key(s) {unknown_mc}")
        n_iterations = mc_data.get("n_iterations", 500)
        if not isinstance(n_iterations, int) or n_iterations <= 0:
            raise CardError("[monte_carlo]: 'n_iterations' must be a positive int")
        seed = mc_data.get("seed", 2024)
        if seed is not None and not isinstance(seed, int):
            raise CardError("[monte_carlo]: 'seed' must be an int or null")
        mc = NumericalOptions(n_iterations=n_iterations, seed=seed)

    datastore = doc.get("datastore", {})
    if not isinstance(datastore, dict):
        raise CardError("'datastore' must be a table")
    unknown_ds = sorted(set(datastore) - {"path"})
    if unknown_ds:
        raise CardError(f"[datastore]: unknown key(s) {unknown_ds}")

    name = study.get("name") or "etude"
    if not isinstance(name, str) or not name:
        raise CardError("[study]: 'name' must be a non-empty string")

    return Card(
        path=path,
        name=name,
        farm=farm,
        model_selection=model_selection,
        variant_grid=dict(variant_grid),
        named_combinations=named_combinations,
        mc=mc,
        datastore_path=datastore.get("path", "results.json"),
        source_sha256=hashlib.sha256(source_text_bytes).hexdigest(),
        source_text=source_text_bytes.decode("utf-8"),
    )


def run_card(
    card: Card,
    params: Optional[ParameterSet] = None,
    registry: Optional[ModelRegistry] = None,
    record: bool = True,
) -> Dict[str, Any]:
    """Execute a study card end to end.

    Runs the same orchestration as a programmatic case study
    (:func:`pblca.scenarios.run_case_study`), from the card's
    farm, variant grid and Monte-Carlo plan, and saves the JSON
    datastore (card metadata embedded for traceability).

    Args:
        card: validated study card.
        params: parameter set (default: the standard traceable set).
        registry: model registry (default: the standard registry).
        record: if True, records the entries and saves the datastore.

    Returns:
        the case-study summary dict, plus the datastore path under
        ``"datastore_path"``.
    """
    from .engine import LCAEngine
    from .scenarios import run_case_study

    engine = LCAEngine(
        params=params,
        registry=registry,
        datastore_path=card.datastore_path,
    )
    summary = run_case_study(engine, card.to_case_study(), record=record)
    if record:
        engine.datastore.card = card.as_dict()
        engine.datastore.card_source = card.source_text
        engine.datastore.save()
    summary["datastore_path"] = card.datastore_path
    return summary
