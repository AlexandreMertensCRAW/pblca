"""PBLCA — Process-Based LCA Engine.

Process-based LCA engine for mixed crop-livestock farms,
ISO 14040/14044 compliant, with:

* three distinct layers: processes (layer 1), gas flows
  CH4/CO2/N2O (layer 2), GWP/GWP* impacts (layer 3);
* a model registry with a universal interface — every variant
  (Tier-2, Tier-3, ...) is testable at every simulation;
* traceable parameters (bibliographic reference) with uncertainties
  propagated by Monte-Carlo, a given parameter keeping the same value
  for all its occurrences (all farms);
* structured JSON storage (one entry per simulation);
* explicit warnings and errors management (diagnostics).

Entry point: :class:`pblca.engine.LCAEngine`.
"""

from .engine import DataStore, LCAEngine, SimulationResult
from .gases import GASES, Emission, GasLedger
from .impacts import GwpStarInputs, characterize, compute_gwpstar
from .params import Parameter, ParameterSet, Reference, build_default_parameter_set
from .registry import (
    AnimalGroup,
    FarmContext,
    LandParcel,
    ModelContext,
    ModelRegistry,
    ModelResult,
    ModelSpec,
    build_default_registry,
)

__version__ = "0.1.0"
__all__ = [
    "LCAEngine",
    "DataStore",
    "SimulationResult",
    "GasLedger",
    "Emission",
    "GASES",
    "GwpStarInputs",
    "characterize",
    "compute_gwpstar",
    "Parameter",
    "ParameterSet",
    "Reference",
    "build_default_parameter_set",
    "AnimalGroup",
    "FarmContext",
    "LandParcel",
    "ModelContext",
    "ModelRegistry",
    "ModelResult",
    "ModelSpec",
    "build_default_registry",
]
