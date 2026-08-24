"""Valuation dispatcher: structure classification decides the engine, not Safe eligibility."""
from __future__ import annotations

from bonds.structures import BondStructure, CouponType, PrincipalType
from .amortizing import calculate_amortizing
from .fixed import calculate_fixed
from .floater import calculate_floater
from .optioned import calculate_optioned
from .perpetual import calculate_perpetual


def calculate_bond_analytics(*, structure: BondStructure, context: dict) -> dict:
    principal = structure.principal_model.type
    coupon = structure.coupon_model.type
    if principal == PrincipalType.PERPETUAL:
        return calculate_perpetual(structure=structure, **context)
    if coupon in {CouponType.RUONIA_FLOAT, CouponType.KEY_RATE_FLOAT, CouponType.KBD_FLOAT, CouponType.OTHER_FLOAT}:
        return calculate_floater(**{key: value for key, value in context.items() if key in {
            "flows", "dirty_price", "clean_price", "aci", "as_of", "discount_curve",
            "reference_curve", "market_value",
        }})
    if principal == PrincipalType.AMORTIZING:
        return calculate_amortizing(**{key: value for key, value in context.items() if key in {
            "flows", "dirty_price", "clean_price", "aci", "as_of", "curve", "market_value",
        }})
    analytics = calculate_fixed(**{key: value for key, value in context.items() if key in {
        "flows", "dirty_price", "clean_price", "aci", "as_of", "curve", "market_value"
    }})
    if structure.optionality.has_put or structure.optionality.has_call:
        ytm = ((analytics.get("ytm_gross") or {}).get("value"))
        analytics.update(calculate_optioned(
            flows=context["flows"], dirty_price=context["dirty_price"], as_of=context["as_of"],
            put_schedule=[dict(item, face=context["face"]) for item in structure.optionality.put_schedule],
            call_schedule=[dict(item, face=context["face"]) for item in structure.optionality.call_schedule],
            maturity_yield=ytm / 100.0 if ytm is not None else None,
        ))
    return analytics
