"""Layer 1 — Enteric methane: Tier-2 and Tier-3 variants.

Four alternative equations registered for the ``enteric_ch4`` slot,
all following the universal interface
``(ModelContext) -> ModelResult`` (interchangeable at every
simulation — alternative-model testability requirement):

* ``tier2_2006``: IPCC 2006 Vol.4 Ch.10, equations 10.3 (NEm), 10.4
  (NEa), 10.6 (NEg), 10.14 (REM), 10.15 (REG), 10.16 (GE) and 10.21
  (EF). EF (kg CH4/head/yr) = GE × Ym / 55.65 with Ym interpolated
  from 6.5 % (grass diet) to 3.0 % (>90 % concentrates), Table 10.12.
* ``tier2_2019``: same IPCC energy chain, Ym from the 2019
  Refinement Table 10.12 (Updated): 7.0 % (grazing systems), 6.3 %
  (mixed) interpolated towards 4.0 % (grain-based feedlot).
* ``tier2_fao_ym``: IPCC 2006 energy chain with a
  digestibility-dependent Ym, as used in the FAO dairy-sector LCA
  (GLEAM): Ym(%) = 9.75 − 0.05 × DE% (FAO 2010).
* ``tier3_mills``: exponential saturation equation (Mills et al.
  2003, via Ellis et al. 2009): CH4 (MJ/d) = a × (1 − e^(−k × DMI)),
  with a = 10.8 MJ/d and k = 0.141 (kg DM)^-1. DMI is estimated with
  the IPCC energy chain (GE / 18.45).

Ration definition: each ``AnimalGroup`` can either rely on the IPCC
energy chain (default) or carry measured ``dmi_measured``/``ge_measured``
values encoded directly from farm data — see ``AnimalGroup`` and
``_energy_chain``. The chosen variant applies to both ration modes.
"""

from __future__ import annotations

from typing import Dict

from ..registry import ModelContext, ModelResult, ModelSpec

REF_T2 = "IPCC 2006, Vol.4 Ch.10, Eq. 10.3/10.4/10.6/10.14/10.15/10.16/10.21 + Table 10.12"
REF_T2_2019 = (
    "IPCC 2019 Refinement, Vol.4 Ch.10, Eq. 10.3-10.16/10.21 "
    "+ Table 10.12 (Updated)"
)
REF_T2_FAO = (
    "FAO 2010, Greenhouse Gas Emissions from the Dairy Sector — A Life "
    "Cycle Assessment (GLEAM): Ym(%) = 9.75 − 0.05 × DE%; energy chain "
    "IPCC 2006 Vol.4 Ch.10, Eq. 10.3-10.16"
)
REF_T3 = "Ellis et al. 2009 (exponential saturation, after Mills et al. 2003)"


def _energy_chain(ctx: ModelContext, g: "ModelContext.farm.animals[0].__class__") -> Dict[str, float]:
    """Gross energy intake and DMI of an animal group.

    Two ration-definition modes (requirement: alternative inputs, e.g.
    on-farm measurements):

    * ``ipcc_equations`` (default): GE is estimated from net energy
      requirements and diet digestibility (Eq. 10.3-10.16), then DMI
      follows as GE / diet_ge_density.
    * ``measured``: if the group carries ``dmi_measured`` and/or
      ``ge_measured`` (farm measurements, e.g. ration sheets), those
      values are used directly instead of the IPCC chain. When only
      one of the two is provided, the other is derived from
      ``diet_ge_density`` (GE = DMI × density, DMI = GE / density). A
      warning is logged when the two provided measures are mutually
      inconsistent (> 10 % departure from GE = DMI × density).

    Args:
        ctx: model context (parameters + current values).
        g: animal group (age class).

    Returns:
        a {ge_mj_day, dmi_kg_day, wg_kg_day, bw_avg, ration_mode}
        dictionary.
    """
    v = ctx.v
    # Average weight over the period and daily gain.
    bw_avg = 0.5 * (g.bw_start + g.bw_end)
    wg_day = (g.bw_end - g.bw_start) / g.days if g.days > 0 else 0.0

    # --- Mode 2: ration encoded directly from farm measurements ------
    if g.ration_mode == "measured":
        dmi = g.dmi_measured
        ge = g.ge_measured
        density = g.diet_ge_density
        if dmi is not None and ge is not None:
            if density > 0 and abs(ge - dmi * density) > 0.10 * max(ge, dmi * density, 1e-9):
                ctx.logger.warn(
                    "enteric",
                    f"Measured GE ({ge:.1f} MJ/d) and DMI ({dmi:.2f} kg/d) "
                    f"inconsistent with the diet energy density "
                    f"({density:.2f} MJ/kg DM) for group {g.key}",
                )
        elif dmi is not None:
            ge = dmi * density if density > 0 else None
            if ge is None:
                ctx.logger.error("enteric", f"diet_ge_density is zero for group {g.key}")
                ge = 0.0
        elif ge is not None:
            dmi = ge / density if density > 0 else None
            if dmi is None:
                ctx.logger.error("enteric", f"diet_ge_density is zero for group {g.key}")
                dmi = 0.0
        if (dmi is not None and dmi <= 0) or (ge is not None and ge <= 0):
            ctx.logger.error(
                "enteric",
                f"Measured intake must be positive for group {g.key} "
                f"(got DMI={dmi}, GE={ge})",
            )
        return {
            "ge_mj_day": ge or 0.0,
            "dmi_kg_day": dmi or 0.0,
            "wg_kg_day": wg_day,
            "bw_avg": bw_avg,
            "ration_mode": "measured",
        }
    # --- Mode 1: IPCC Tier-2 energy chain ----------------------------

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
    return {
        "ge_mj_day": ge,
        "dmi_kg_day": dmi,
        "wg_kg_day": wg_day,
        "bw_avg": bw_avg,
        "ration_mode": "ipcc_equations",
    }


def _ym_2006(v, g) -> float:
    """Ym per IPCC 2006 Table 10.12, interpolated on the concentrate
    share: 6.5 % (grass diet) towards 3.0 % (>90 % concentrates)."""
    ym_high = v("ym_grass_diet")
    ym_low = v("ym_feedlot")
    return ym_high - (ym_high - ym_low) * min(max(g.share_concentrate, 0.0), 1.0)


def _ym_2019(ctx: ModelContext, v, g) -> float:
    """Ym per IPCC 2019 Refinement Table 10.12 (Updated), other cattle.

    7.0 % for grazing systems, 6.3 % for mixed systems, interpolated
    towards 4.0 % for grain-based feedlot diets as the concentrate
    share increases. A warning is logged when the interpolation leaves
    the tabulated domain (pure grazing / pure feedlot).
    """
    ym_grazing = v("ym_2019_grazing")
    ym_mixed = v("ym_2019_mixed")
    ym_feedlot = v("ym_2019_feedlot")
    sc = min(max(g.share_concentrate, 0.0), 1.0)
    if sc <= 0.5:
        # grazing -> mixed: assumed linear on the concentrate share.
        ym = ym_grazing + (ym_mixed - ym_grazing) * (sc / 0.5)
    else:
        # mixed -> feedlot.
        ym = ym_mixed + (ym_feedlot - ym_mixed) * ((sc - 0.5) / 0.5)
    if sc > 0.9:
        ctx.logger.warn(
            "enteric",
            f"Concentrate share {sc:.2f} outside the tabulated mixed/feedlot "
            f"domain of Table 10.12 (2019) for group {g.key} — interpolated Ym",
        )
    return ym


def _ym_fao(ctx: ModelContext, v, g) -> float:
    """Digestibility-dependent Ym per the FAO dairy-sector LCA (GLEAM):

    Ym(%) = 9.75 − 0.05 × DE% with DE% the diet digestibility in
    percentage points. Bounded to [1.5 %, 12 %] (physical domain); a
    warning is logged when the raw equation leaves that domain.
    """
    de_pct = g.diet_de * 100.0
    ym_pct = v("ym_fao_intercept") - v("ym_fao_slope") * de_pct
    if not 1.5 <= ym_pct <= 12.0:
        ctx.logger.warn(
            "enteric",
            f"FAO Ym equation gives {ym_pct:.2f} % outside the physical "
            f"domain [1.5, 12] for group {g.key} — bounded",
        )
        ym_pct = min(max(ym_pct, 1.5), 12.0)
    return ym_pct / 100.0


def _enteric_ge_ym(ctx: ModelContext, model_name: str, ym_func) -> ModelResult:
    """Shared implementation of the GE × Ym variants (Eq. 10.21).

    Args:
        ctx: model context (farm, parameters, log).
        model_name: variant name (traceability).
        ym_func: (v, group) -> Ym fraction, specific to the variant.

    Returns:
        ModelResult with the total ch4_kg and a trace per age class.
    """
    v = ctx.v
    res = ModelResult(model_name=model_name)
    res.trace["per_group"] = {}
    total = 0.0
    for g in ctx.farm.animals:
        e = _energy_chain(ctx, g)
        ym = ym_func(ctx, v, g) if ym_func.__code__.co_argcount == 3 else ym_func(v, g)
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


def enteric_tier2_2006(ctx: ModelContext) -> ModelResult:
    """Enteric methane, IPCC 2006 Tier-2 (Eq. 10.21, Table 10.12).

    EF = GE × Ym / 55.65, Ym interpolated from 6.5 % (grass diet) to
    3.0 % (>90 % concentrates) on the concentrate share.

    Args:
        ctx: model context (farm, parameters, log).

    Returns:
        ModelResult with the total ch4_kg and a trace per age class.
    """
    return _enteric_ge_ym(ctx, "tier2_2006", _ym_2006)


def enteric_tier2_2019(ctx: ModelContext) -> ModelResult:
    """Enteric methane, IPCC 2019 Refinement Tier-2.

    Same IPCC energy chain as 2006 (Eq. 10.3-10.16 unchanged by the
    refinement for growing cattle), but Ym follows the 2019 Table
    10.12 (Updated): grazing 7.0 %, mixed 6.3 %, grain feedlot 4.0 %.

    Args:
        ctx: model context (farm, parameters, log).

    Returns:
        ModelResult with the total ch4_kg and a trace per age class.
    """
    return _enteric_ge_ym(ctx, "tier2_2019", _ym_2019)


def enteric_tier2_fao_ym(ctx: ModelContext) -> ModelResult:
    """Enteric methane, IPCC 2006 chain with FAO digestibility-dependent Ym.

    Ym(%) = 9.75 − 0.05 × DE% (FAO 2010 dairy-sector LCA, GLEAM),
    so that the methane conversion factor responds directly to the
    ration digestibility instead of a fixed tabulated default.

    Args:
        ctx: model context (farm, parameters, log).

    Returns:
        ModelResult with the total ch4_kg and a trace per age class.
    """
    return _enteric_ge_ym(ctx, "tier2_fao_ym", _ym_fao)


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
        variant="tier2_2006",
        tier="Tier-2",
        func=enteric_tier2_2006,
        reference=REF_T2,
        description=(
            "IPCC 2006: GE from net energy requirements; EF = GE×Ym/55.65, "
            "Ym 6.5→3.0 % on the concentrate share (Table 10.12)."
        ),
    ),
    ModelSpec(
        slot="enteric_ch4",
        variant="tier2_2019",
        tier="Tier-2",
        func=enteric_tier2_2019,
        reference=REF_T2_2019,
        description=(
            "IPCC 2019 Refinement: same energy chain, Ym from Table 10.12 "
            "(Updated): grazing 7.0 %, mixed 6.3 %, grain feedlot 4.0 %."
        ),
    ),
    ModelSpec(
        slot="enteric_ch4",
        variant="tier2_fao_ym",
        tier="Tier-2",
        func=enteric_tier2_fao_ym,
        reference=REF_T2_FAO,
        description=(
            "FAO dairy LCA (GLEAM): Ym(%) = 9.75 − 0.05 × DE%, applied on "
            "the IPCC 2006 energy chain."
        ),
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
