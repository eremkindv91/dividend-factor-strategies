"""Callable/resettable perpetual scenarios. Never computes maturity YTM."""
from __future__ import annotations

from datetime import date

from bonds.cashflows import CashFlow
from bonds.structures import BondStructure
from .common import metric
from .optioned import calculate_optioned


def simple_extension_proxy(reference_rate_pct: float, contractual_margin_bp: float,
                           required_spread_bp: float) -> float:
    coupon_rate = reference_rate_pct + contractual_margin_bp / 100.0
    required = reference_rate_pct + required_spread_bp / 100.0
    if required <= 0:
        raise ValueError("required yield must be positive")
    return 100.0 * coupon_rate / required


def calculate_perpetual(*, structure: BondStructure, flows: list[CashFlow], clean_price_pct: float,
                        dirty_price: float, face: float, annual_coupon: float,
                        as_of: date, reference_rate_pct: float | None = None,
                        required_spread_bp: float | None = None) -> dict:
    current_yield = annual_coupon / (clean_price_pct * face / 100.0) * 100.0 if clean_price_pct > 0 else None
    call_schedule = [dict(item, face=face) for item in structure.optionality.call_schedule]
    optioned = calculate_optioned(
        flows=flows, dirty_price=dirty_price, as_of=as_of,
        put_schedule=[], call_schedule=call_schedule, maturity_yield=None,
    )
    analytics = {
        "ytm_gross": metric(None, "pct", "not_applicable_perpetual", as_of, {}, "NOT_APPLICABLE"),
        "current_yield": metric(current_yield, "pct", "annual_coupon_over_clean_price", as_of,
                                {"annual_coupon": annual_coupon, "clean_price_pct": clean_price_pct}),
        "ytc_if_call": optioned["ytc_if_call"],
        "oas": optioned["oas"],
    }
    model = structure.coupon_model
    if reference_rate_pct is not None and required_spread_bp is not None and model.contractual_margin_bp is not None:
        proxy = simple_extension_proxy(reference_rate_pct, model.contractual_margin_bp, required_spread_bp)
        analytics["extension_price_proxy"] = metric(
            proxy, "pct_nominal", "simple_extension_proxy", as_of,
            {"reference_rate_pct": reference_rate_pct, "contractual_margin_bp": model.contractual_margin_bp,
             "required_spread_bp": required_spread_bp}, "SCENARIO",
        )
    else:
        analytics["extension_price_proxy"] = metric(None, "pct_nominal", "simple_extension_proxy", as_of, {}, "UNAVAILABLE")
    return analytics
