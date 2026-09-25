"""Layer 1 — Soil N2O (direct + indirect).

``soil_n2o`` slot: N2O from nitrogen inputs to soil (synthetic
fertilisers, spread manure, crop residues, excreta deposited at
pasture), direct emissions (EF1, Eq. 11.1) and indirect emissions
(volatilisation/re-deposition EF4 and leaching EF5, Eq. 11.9/11.11 —
IPCC 2019 Refinement Ch.11).

A Tier-3 variant (e.g. a DNDC/RothC-like N2O model) can be registered
under the same slot: the universal interface requires it.
"""

from __future__ import annotations

from ..registry import ModelContext, ModelResult, ModelSpec

REF = "IPCC 2019 Refinement, Vol.4 Ch.11, Eq. 11.1, 11.9, 11.11 (Table 11.1/11.3)"


def soil_n2o(ctx: ModelContext) -> ModelResult:
    """Direct and indirect N2O from the farm's agricultural soils.

    Consistency note: the spread organic nitrogen originates from the
    manure module (resolved beforehand) via
    ``ctx.values["__n_organic_spread__"]`` if present, otherwise from
    the static parcel values.

    Returns:
        ModelResult with the total n2o_kg and a direct/indirect trace.
    """
    v = ctx.v
    res = ModelResult(model_name="ipcc_2019")
    cv = v("n2o_n_to_n2o")
    direct = 0.0
    indirect_volat = 0.0
    indirect_leach = 0.0
    res.trace["per_parcel"] = {}

    for p in ctx.farm.parcels:
        f_syn = p.n_synthetic * p.area
        f_org = p.n_organic_spread * p.area
        f_res = p.n_residue * p.area
        f_prp = p.n_excreta_grazing * p.area
        f_total = f_syn + f_org + f_res + f_prp
        if f_total == 0 and p.area == 0:
            continue
        # Direct (Eq. 11.1)
        d = f_total * v("ef1_soil")
        # Indirect volatilisation (Eq. 11.9): FracGASF for synthetic,
        # FracGASM for organic/residues/field deposits
        vol = (
            f_syn * v("frac_gasf")
            + (f_org + f_res + f_prp) * v("frac_gasm")
        ) * v("ef4_deposition")
        # Indirect leaching (Eq. 11.11)
        lea = f_total * v("frac_leach") * v("ef5_leaching")
        direct += d * cv
        indirect_volat += vol * cv
        indirect_leach += lea * cv
        res.trace["per_parcel"][p.key] = {
            "n_input_kg": f_total,
            "n2o_direct_kg": d * cv,
            "n2o_indirect_kg": (vol + lea) * cv,
        }
    res.n2o_kg = direct + indirect_volat + indirect_leach
    res.trace["totals"] = {
        "direct": direct,
        "indirect_volatilisation": indirect_volat,
        "indirect_leaching": indirect_leach,
    }
    return res


SPECS = [
    ModelSpec(
        slot="soil_n2o",
        variant="ipcc_2019",
        tier="Tier-1/2",
        func=soil_n2o,
        reference=REF,
        description="Soil N2O: direct EF1 + indirect EF4/EF5 (2019 Refinement).",
    ),
]
