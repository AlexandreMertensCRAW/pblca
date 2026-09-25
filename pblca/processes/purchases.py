"""Layer 1 — Indirect emissions from purchases (upstream of the farm).

``purchases`` slot: upstream footprint of purchased inputs —
animals (calves), feed (concentrates, co-products), fertilisers and
amendments (N, P, K, lime, seeds). Upstream emission factors are
kg CO2e per unit; they are decomposed into CO2-equivalent gases only
(upstream sources are mostly energy-related CO2), with a minor N2O and
CH4 share neglected for transparency.

"From purchase onwards" requirement: the gases of each purchase are
traced in the ledger (layer 2) with the reference of the database used
(Agribalyse / Ecoinvent), ready for GWP and GWP*.
"""

from __future__ import annotations

from ..registry import ModelContext, ModelResult, ModelSpec

REF = "Agribalyse 3.0.1 / Ecoinvent 3 (upstream factors, traceability per parameter)"


def purchases_upstream(ctx: ModelContext) -> ModelResult:
    """Upstream footprint of the farm's annual purchases.

    Expected keys of ``farm.purchases`` (units in the parameters):
      * ``n_calves_purchased`` (heads) → ef_purchase_calf (kg CO2e/kg liveweight purchased)
      * ``concentrate_kg_dm`` (kg DM) → ef_concentrate_feed
      * ``coproduct_kg_dm`` (kg DM) → ef_copproduct_feed
      * ``synthetic_n_kg`` (kg N) → ef_synthetic_n
      * ``lime_kg`` (kg) → ef_lime
      * ``p2o5_kg``, ``k2o_kg``, ``seeds_kg`` → dedicated factors

    Returns:
        ModelResult with the total co2_kg and a trace per purchase item.
    """
    v = ctx.v
    res = ModelResult(model_name="agribalyse_ecoinvent")
    p = ctx.farm.purchases
    total = 0.0
    res.trace["items"] = {}

    def add(pid: str, key: str) -> None:
        nonlocal total
        if key in p and p[key] > 0:
            co2 = p[key] * v(pid)
            res.trace["items"][key] = {"co2e_kg": co2, "ef_pid": pid}
            total += co2

    add("ef_purchase_calf", "n_calves_purchased_kg_lw")
    add("ef_concentrate_feed", "concentrate_kg_dm")
    add("ef_copproduct_feed", "coproduct_kg_dm")
    add("ef_synthetic_n", "synthetic_n_kg")
    add("ef_lime", "lime_kg")
    add("ef_p2o5_fertilizer", "p2o5_kg")
    add("ef_k2o_fertilizer", "k2o_kg")
    add("ef_seed", "seeds_kg")

    res.co2_kg = total
    return res


SPECS = [
    ModelSpec(
        slot="purchases",
        variant="agribalyse_ecoinvent",
        tier="Tier-1 (upstream factors)",
        func=purchases_upstream,
        reference=REF,
        description="Purchases: calves, feed, fertilisers, amendments, seeds.",
    ),
]
