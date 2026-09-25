"""Layer 2 — gas flows: CH4 / CO2 / N2O ledger.

Requirement: "keep track of the different gases emitted throughout the
modelling (from purchase onwards) in order to compute various indicators
such as GWP and GWP*."

Each emission is recorded as an inventory entry with:
  - the gas (CH4, CO2, N2O),
  - the amount (kg/yr),
  - the source subsystem (e.g. "enteric", "manure_ch4", "purchase_feed"),
  - the emitting farm,
  - the model used (Tier-2/Tier-3 variant, traceability),
  - the bibliographic reference of the equation or emission factor.

The inventory stays in physical units (kg of each gas): characterisation
(GWP, GWP*) happens in layer 3 only, never altering the inventory. GWP
and GWP* indicators can therefore be computed or recomputed a posteriori.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

GASES = ("CH4", "CO2", "N2O")


@dataclass
class Emission:
    """A traced gas emission (one inventory line).

    Attributes:
        gas: "CH4", "CO2" or "N2O".
        amount_kg: amount emitted (kg/yr).
        source: source subsystem (e.g. "enteric", "soil_n2o_direct").
        farm_id: emitting farm.
        model: model variant used (e.g. "tier3_mills").
        reference: bibliographic reference of the equation/factor.
        detail: free sub-detail (e.g. age class, parcel).
    """

    gas: str
    amount_kg: float
    source: str
    farm_id: str
    model: str
    reference: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.gas not in GASES:
            raise ValueError(f"Unknown gas: {self.gas}")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "gas": self.gas,
            "amount_kg": self.amount_kg,
            "source": self.source,
            "farm_id": self.farm_id,
            "model": self.model,
            "reference": self.reference,
            "detail": self.detail,
        }


class GasLedger:
    """Gas emissions ledger (layer 2).

    Aggregates all emissions of all farms of a simulation, with their
    full traceability. Values are summable per gas, per source, per
    farm — for LCA (ISO 14044: complete inventory before
    characterisation).
    """

    def __init__(self) -> None:
        self._entries: List[Emission] = []

    # ------------------------------------------------------------------
    def add(
        self,
        gas: str,
        amount_kg: float,
        source: str,
        farm_id: str,
        model: str,
        reference: str,
        detail: str = "",
    ) -> None:
        """Add a traced emission to the ledger.

        Raises:
            ValueError: if the gas is not CH4/CO2/N2O.
        """
        if amount_kg < 0:
            # A legitimate negative flux (carbon sink) is allowed for
            # CO2 via ``add_sink``; otherwise it is a modelling error.
            if not (gas == "CO2" and "sink" in source):
                raise ValueError(
                    f"Negative emission forbidden ({gas}, {source}, {amount_kg})"
                )
        self._entries.append(
            Emission(gas, float(amount_kg), source, farm_id, model, reference, detail)
        )

    def add_sink(
        self,
        amount_kg: float,
        source: str,
        farm_id: str,
        model: str,
        reference: str,
        detail: str = "",
    ) -> None:
        """Add a CO2 sink (carbon storage, negative value)."""
        if amount_kg > 0:
            raise ValueError("A sink must be negative or zero (CO2).")
        self._entries.append(
            Emission("CO2", float(amount_kg), source, farm_id, model, reference, detail)
        )

    # ------------------------------------------------------------------
    def entries(self) -> List[Emission]:
        return list(self._entries)

    def total(self, gas: str) -> float:
        """Total emitted for a gas, all sources combined (kg/yr)."""
        return sum(e.amount_kg for e in self._entries if e.gas == gas)

    def total_by_source(self) -> Dict[str, Dict[str, float]]:
        """Source -> gas -> kg/yr matrix (contribution analysis)."""
        out: Dict[str, Dict[str, float]] = {}
        for e in self._entries:
            out.setdefault(e.source, {"CH4": 0.0, "CO2": 0.0, "N2O": 0.0})
            out[e.source][e.gas] += e.amount_kg
        return out

    def total_by_farm(self, farm_id: str) -> Dict[str, float]:
        """Per-gas totals for one farm (kg/yr)."""
        return {
            gas: sum(
                e.amount_kg
                for e in self._entries
                if e.gas == gas and e.farm_id == farm_id
            )
            for gas in GASES
        }

    def merge(self, other: "GasLedger") -> None:
        """Merge another ledger (multi-farm)."""
        self._entries.extend(other._entries)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "totals_kg": {gas: self.total(gas) for gas in GASES},
            "by_source": {
                src: {g: v for g, v in d.items() if v != 0}
                for src, d in self.total_by_source().items()
            },
            "entries": [e.as_dict() for e in self._entries],
        }

    def __len__(self) -> int:
        return len(self._entries)
