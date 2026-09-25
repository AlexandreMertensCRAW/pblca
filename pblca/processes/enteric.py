"""Layer 1 — Enteric methane: Tier-2 and Tier-3 variants.

Two alternative equations registered for the ``enteric_ch4`` slot:

* ``tier2``: IPCC 2006 Vol.4 Ch.10, equations 10.3 (NEm), 10.4 (NEa),
  10.6 (NEg), 10.14 (REM), 10.15 (REG), 10.16 (GE) and 10.21 (EF).
  EF (kg CH4/head/yr) = GE × Ym / 55.65.
* ``tier3_mills``: exponential saturation equation (Mills et al. 2003,
  via Ellis et al. 2009): CH4 (MJ/d) = a × (1 − e^(−k × DMI)),
  with a = 10.8 MJ/d and k = 0.141 (kg DM)^-1. DMI is estimated with
  the IPCC energy chain (GE / 18.45).

Both variants follow the universal interface
``(ModelContext) -> ModelResult`` and are therefore interchangeable at
every simulation (alternative-model testability requirement).
"""

from __future__ import annotations

from typing import Dict

from ..registry import ModelContext, ModelResult, ModelSpec

REF_T2 = "IPCC 2006, Vol.4 Ch.10, Eq. 10.3/10.4/10.6/10.14/10.15/10.16/10.21"
REF_T3 = "Ellis et al. 2009 (exponential saturation, after Mills et al. 2003)"


def _energy_chain(ctx: ModelContext, g: "ModelContext.farm.animals[0].__class__") -> Dict[str, float]:
    """IPCC Tier-2 energy chain for an animal group.

    Estimates GE (MJ/head/d) from net energy requirements and diet
    digestibility (Eq. 10.3–10.16), then the associated DMI.

    Args:
        ctx: model context (parameters + current values).
        g: animal group (age class).

    Returns:
        a {ge_mj_day, dmi_kg_day, wg_kg_day, bw_avg} dictionary.
    """
    v = ctx.v
    # Average weight over the period and daily gain.
    bw_avg = 0.5 * (g.bw_start + g.bw_end)
    wg_day = (g.bw_end - g.bw_start) / g.days if g.days > 0 else 0.0

    # Eq. 10.3: NEm = Cfi * BW^0.75 (Cfi = 0.322 for growing cattle)
    nem = v("cfi_growing_cattle") * bw_avg ** 0.75
    # Eq. 10.4: NEa = 0.17 * NEM (grazing) modulated by the fraction of the year at pasture
    nea = v("ca_activity_grazing") * nem * g.grazing
    # Eq. 10.6: NEg = 22.02 * (BW/(C*MW))^0.75 * WG^1.097; C = 1 (castrated males)
    # MW: mature weight (here set by the context, default 700 kg beef crossbreed)
    mw = ctx.farm.__dict__.get("mature_weight", 700.0)
    neg = 0.0
    if wg_day > 0:
        neg = 22.02 * (bw_avg / mw) ** 0.75 * wg_day ** 1.097

    # Eq. 10.14: REM = 1.123 − 4.092e-3·DE% + 1.126e-5·DE%² − 25.4/DE%
    # Eq. 10.15: REG = 1.164 − 5.160e-3·DE% + 1.308e-5·DE%² − 37.4/DE%
    # DE% is digestibility in PERCENT (diet_de is a fraction).
    de_pct = g.diet_de * 100.0
    if de_pct <= 0:
        ctx.logger.error("enteric", f"DE% is zero for group {g.key}")
        rem = reg = 1e-6
    else:
        rem = 1.123 - 4.092e-3 * de_pct + 1.126e-5 * de_pct**2 - 25.4 / de_pct
        reg = 1.164 - 5.160e-3 * de_pct + 1.308e-5 * de_pct**2 - 37.4 / de_pct
    if rem <= 0 or reg <= 0 or rem > 1 or reg > 1:
        ctx.logger.warn(
            "enteric",
            f"REM/REG outside physical domain for DE%={de_pct:.1f} "
            f"(group {g.key}) — equations 10.14/10.15 extrapolated",
        )
    # Physical bounding: REM/REG are NE/DE ratios ∈ ]0,1].
    rem = min(max(rem, 1e-6), 1.0)
    reg = min(max(reg, 1e-6), 1.0)

    # Eq. 10.16: GE = [(NEm+NEa+NEl+NEwork+NEp)/REM + NEg/REG] / (DE% / 100)
    ge = 0.0
    if de_pct > 0:
        ge = ((nem + nea) / rem + (neg / reg if neg > 0 else 0.0)) / (de_pct / 100.0)
    if ge <= 0:
        ctx.logger.error("enteric", f"GE is zero for group {g.key}")
    dmi = ge / g.diet_ge_density if g.diet_ge_density > 0 else 0.0
    return {"ge_mj_day": ge, "dmi_kg_day": dmi, "wg_kg_day": wg_day, "bw_avg": bw_avg}


def enteric_tier2(ctx: ModelContext) -> ModelResult:
    """Enteric methane with the IPCC Tier-2 method (Eq. 10.21).

    EF = GE × Ym / 55.65, Ym depending on the concentrate share of the
    diet (0.065 for a grass-based diet, interpolated towards 0.03 for
    finishing).

    Args:
        ctx: model context (farm, parameters, log).

    Returns:
        ModelResult with the total ch4_kg and a trace per age class.
    """
    v = ctx.v
    res = ModelResult(model_name="tier2")
    res.trace["per_group"] = {}
    total = 0.0
    for g in ctx.farm.animals:
        e = _energy_chain(ctx, g)
        # Interpolated Ym: 6.5 % (grass) -> 3 % (finishing >90 % concentrates)
        ym_high = v("ym_grass_diet")
        ym_low = v("ym_feedlot")
        ym = ym_high - (ym_high - ym_low) * min(g.share_concentrate, 1.0)
        # Eq. 10.21: EF kg CH4/head/yr = (GE×Ym/55.65) × days
        ef_kg = e["ge_mj_day"] * ym / v("energy_ch4_mj_per_kg") * g.days
        group_ch4 = ef_kg * g.n_head
        total += group_ch4
        res.trace["per_group"][g.key] = {
            "ge_mj_day": e["ge_mj_day"],
            "dmi_kg_day": e["dmi_kg_day"],
            "ym": ym,
            "ch4_kg": group_ch4,
            "n_head": g.n_head,
        }
        res.fluxes[f"ge_{g.key}"] = e["ge_mj_day"] * g.days * g.n_head
    res.ch4_kg = total
    return res


def enteric_tier3_mills(ctx: ModelContext) -> ModelResult:
    """Enteric methane with a Tier-3 equation (Mills et al. 2003).

    CH4 (MJ/d) = a × (1 − e^(−k × DMI)) with a = 10.8, k = 0.141; DMI
    comes from the IPCC energy chain (consistency of assumptions across
    variants). The equation enforces CH4 = 0 at DMI = 0 and an
    asymptotic plateau at high intake levels.

    Args:
        ctx: model context.

    Returns:
        ModelResult with the total ch4_kg and a trace per age class.
    """
    import math

    v = ctx.v
    a = v("t3_mills_max_ch4")
    k = v("t3_mills_k")
    res = ModelResult(model_name="tier3_mills")
    res.trace["per_group"] = {}
    total = 0.0
    for g in ctx.farm.animals:
        e = _energy_chain(ctx, g)
        dmi = e["dmi_kg_day"]
        if dmi > 25:
            ctx.logger.warn(
                "enteric_t3",
                f"DMI={dmi:.1f} kg/d outside the calibration domain of Mills et al. (group {g.key})",
            )
        ch4_mj_day = a * (1.0 - math.exp(-k * dmi))
        group_ch4 = ch4_mj_day / v("energy_ch4_mj_per_kg") * g.days * g.n_head
        total += group_ch4
        res.trace["per_group"][g.key] = {
            "dmi_kg_day": dmi,
            "ch4_mj_day": ch4_mj_day,
            "ch4_kg": group_ch4,
            "n_head": g.n_head,
        }
    res.ch4_kg = total
    return res


SPECS = [
    ModelSpec(
        slot="enteric_ch4",
        variant="tier2",
        tier="Tier-2",
        func=enteric_tier2,
        reference=REF_T2,
        description="GE from net energy requirements; EF = GE×Ym/55.65.",
    ),
    ModelSpec(
        slot="enteric_ch4",
        variant="tier3_mills",
        tier="Tier-3",
        func=enteric_tier3_mills,
        reference=REF_T3,
        description="Exponential saturation CH4 = 10.8×(1−e^(−0.141×DMI)).",
    ),
]
