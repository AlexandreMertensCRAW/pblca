"""Layer 1 — Farm manure management: CH4 and N2O.

Covers:
  * manure CH4 (IPCC 2006 Eq. 10.22/10.23) — solid storage and
    pasture deposition (permanent grassland), via VS (Eq. 10.24),
  * direct manure N2O (Eq. 10.25, EF3),
  * indirect manure N2O (volatilisation Eq. 10.27, leaching Eq. 10.29),
  * nitrogen balance: the non-emitted fraction returns to the soil as
    spread organic fertiliser (consistency between the manure and soil
    N2O subsystems).

A single ``ipcc_tier2`` variant is registered for each of the
``manure_ch4`` and ``manure_n2o`` slots; the universal interface allows
Tier-3 variants to be added (e.g. a dynamic storage model).
"""

from __future__ import annotations

from ..registry import ModelContext, ModelResult, ModelSpec

REF = "IPCC 2006/2019, Vol.4 Ch.10, Eq. 10.22-10.34"


def _per_group_fluxes(ctx: ModelContext) -> dict:
    """Elementary fluxes per animal group (VS, N intake, N excreted).

    Uses the shared energy chain (layer 1) to guarantee the
    enteric ↔ manure consistency recommended by the IPCC (same GE/DMI).

    Returns:
        a {group_key: {vs_kg_day, n_intake_kg_day, n_excreta_kg_day}} dict.
    """
    from .enteric import _energy_chain

    v = ctx.v
    out = {}
    for g in ctx.farm.animals:
        e = _energy_chain(ctx, g)
        dmi = e["dmi_kg_day"]
        ge = e["ge_mj_day"]
        # Eq. 10.24: VS = GE×(1−DE% + (UE×GE))×(1−ash)/18.45  [kg DM/head/d]
        vs = (
            ge
            * (1.0 - g.diet_de + v("ue_fraction_ge"))
            * (1.0 - v("ash_fraction_manure"))
            / 18.45
        )
        # Eq. 10.32: N intake = DMI × CP / 6.25 (CP as a DM fraction;
        # nitrogen is 16 % of protein, hence the 6.25).
        n_intake = dmi * v("cp_feed") / 6.25  # kg N/head/d
        # Eq. 10.33: N excreted = N intake − N retained (~10 % retention
        # for slow growth on grass; neglected here as a conservative choice)
        n_excreta = n_intake
        out[g.key] = {
            "vs_kg_day": vs,
            "n_excreta_kg_day": n_excreta,
            "grazing": g.grazing,
        }
    return out


def manure_ch4(ctx: ModelContext) -> ModelResult:
    """CH4 from manure management (IPCC Eq. 10.22/10.23).

    CH4 = VS × B0 × MCF, split between pasture deposition (MCF PRP)
    and solid storage (solid MCF, manure collected in buildings).

    Returns:
        ModelResult with the total ch4_kg and a trace per system.
    """
    v = ctx.v
    res = ModelResult(model_name="ipcc_tier2")
    total = 0.0
    res.trace["systems"] = {}
    fluxes = _per_group_fluxes(ctx)
    for key, fl in fluxes.items():
        vs_year = fl["vs_kg_day"] * 365.0  # kg VS/head/yr
        for system, share in ctx.farm.manure_split.items():
            if share == 0:
                continue
            if system == "pasture":
                mcf = v("mcf_prp")
            elif system == "solid_storage":
                mcf = v("mcf_solid_storage")
            else:
                ctx.logger.error("manure_ch4", f"Unknown manure system: {system}")
                continue
            # retrieve the headcount of the group
            n_head = next(a.n_head for a in ctx.farm.animals if a.key == key)
            ch4 = vs_year * v("bo_cattle_manure") * mcf * share * n_head
            res.trace["systems"].setdefault(system, 0.0)
            res.trace["systems"][system] += ch4
            total += ch4
    res.ch4_kg = total
    res.fluxes["vs_total_kg"] = sum(
        fl["vs_kg_day"] * 365.0 * next(a.n_head for a in ctx.farm.animals if a.key == k)
        for k, fl in fluxes.items()
    )
    return res


def manure_n2o(ctx: ModelContext) -> ModelResult:
    """Direct + indirect N2O from manure management (Eq. 10.25-10.29).

    Direct N2O = Σ Nex × EF3(S); indirect = Nex × FracGas × EF4
    (atmospheric deposition) and Nex × FracLeach × EF5 (leaching),
    per system.

    Returns:
        ModelResult with the total n2o_kg; the spread organic nitrogen
        exported to the soil in ``fluxes["n_organic_available"]``
        (layer-1 consistency).
    """
    v = ctx.v
    res = ModelResult(model_name="ipcc_tier2")
    cv = v("n2o_n_to_n2o")
    total_direct = 0.0
    total_indirect = 0.0
    n_available = 0.0
    n_prp_total = 0.0  # nitrogen deposited at pasture (kg N/yr)
    fluxes = _per_group_fluxes(ctx)
    res.trace["direct"] = {}
    res.trace["indirect"] = {}
    for key, fl in fluxes.items():
        n_head = next(a.n_head for a in ctx.farm.animals if a.key == key)
        nex_year = fl["n_excreta_kg_day"] * 365.0 * n_head  # kg N/yr
        for system, share in ctx.farm.manure_split.items():
            if share == 0:
                continue
            nex_s = nex_year * share
            if system == "pasture":
                ef3 = v("ef3_prp_cattle")
                # Pasture deposition: FracGASM volatilisation then EF4;
                # the remainder reaches the soil as an organic input
                # (EF3PRP already accounted at pasture, no extra spreading).
                volat = nex_s * v("frac_gasm")
                leach = nex_s * v("frac_leach")
                ind = volat * v("ef4_deposition") + leach * v("ef5_leaching")
                direct = nex_s * ef3
                n_prp_total += nex_s
                # Volatilised/leached nitrogen does not return to the soil
            elif system == "solid_storage":
                ef3 = v("ef3_solid_storage")
                volat = nex_s * v("frac_gasms_solid")
                leach = nex_s * v("frac_leachms_solid")
                ind = volat * v("ef4_deposition") + leach * v("ef5_leaching")
                direct = nex_s * ef3
                # Stored manure later spread: nitrogen remaining after losses
                n_stored = nex_s * (1.0 - ctx.farm.manure_exported)
                n_available += n_stored - volat - leach
            else:
                ctx.logger.error("manure_n2o", f"Unknown manure system: {system}")
                continue
            total_direct += direct * cv
            total_indirect += ind * cv
            res.trace["direct"].setdefault(system, 0.0)
            res.trace["direct"][system] += direct * cv
            res.trace["indirect"].setdefault(system, 0.0)
            res.trace["indirect"][system] += ind * cv
    if n_available < 0:
        ctx.logger.warn(
            "manure_n2o", "Spread organic nitrogen is negative (losses > inputs)"
        )
        n_available = 0.0
    res.n2o_kg = total_direct + total_indirect
    res.fluxes["n_organic_available"] = max(n_available, 0.0)
    res.fluxes["n_prp_total"] = n_prp_total
    return res


SPECS = [
    ModelSpec(
        slot="manure_ch4",
        variant="ipcc_tier2",
        tier="Tier-2",
        func=manure_ch4,
        reference=REF,
        description="CH4 = VS × B0 × MCF (solid storage + pasture).",
    ),
    ModelSpec(
        slot="manure_n2o",
        variant="ipcc_tier2",
        tier="Tier-2",
        func=manure_n2o,
        reference=REF,
        description="Direct N2O (EF3) + indirect (FracGas/EF4, FracLeach/EF5).",
    ),
]
