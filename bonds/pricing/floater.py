"""Floating-rate projection and Discount Margin analytics."""
from __future__ import annotations

from datetime import date, datetime
import math

from scipy.optimize import brentq

from bonds.cashflows import CashFlow, normalized_flows, year_fraction
from bonds.curves import CurveProvider
from bonds.structures import CouponModel
from .common import metric, present_value


def _date(value: str) -> date:
    return datetime.fromisoformat(value[:10]).date()


def project_floater_cashflows(*, payment_dates: list[str], face: float, coupon_model: CouponModel,
                              as_of: date, maturity_date: str,
                              reference_curve: CurveProvider) -> list[CashFlow]:
    if coupon_model.formula_confidence != "CONFIRMED" or coupon_model.contractual_margin_bp is None:
        raise ValueError("confirmed reference formula and margin are required")
    dates = sorted({_date(value) for value in payment_dates if _date(value) > as_of})
    maturity = _date(maturity_date)
    if maturity > as_of and maturity not in dates:
        dates.append(maturity)
    previous = as_of
    flows: list[CashFlow] = []
    margin_pct = coupon_model.contractual_margin_bp / 100.0
    for payment_date in dates:
        tenor = max(year_fraction(as_of, payment_date), 1 / 365)
        reference = reference_curve.rate_pct(tenor)
        rate = reference + margin_pct
        if coupon_model.floor_pct is not None:
            rate = max(rate, coupon_model.floor_pct)
        if coupon_model.cap_pct is not None:
            rate = min(rate, coupon_model.cap_pct)
        accrual = max(year_fraction(previous, payment_date), 0.0)
        coupon = face * rate / 100.0 * accrual
        flows.append(CashFlow(
            date=payment_date, cashflow_type="coupon", coupon=coupon,
            reference_rate=reference, contractual_margin=coupon_model.contractual_margin_bp,
            coupon_rate=rate, source=reference_curve.source, model_flag="projected",
        ))
        previous = payment_date
    if flows and flows[-1].date == maturity:
        last = flows[-1]
        flows[-1] = CashFlow(**{**last.__dict__, "cashflow_type": "coupon_principal", "principal": face})
    return flows


def calculate_floater(*, flows: list[CashFlow], dirty_price: float, clean_price: float,
                      aci: float, as_of: date, discount_curve: CurveProvider,
                      reference_curve: CurveProvider, market_value: float | None = None) -> dict:
    future = normalized_flows(flows, as_of)
    if not future or dirty_price <= 0:
        return {"discount_margin": metric(None, "bp", "projected_floater_dm", as_of, {}, "UNAVAILABLE")}

    def residual(dm_bp: float, curve: CurveProvider = discount_curve) -> float:
        return present_value(future, as_of, lambda t: curve.discount_factor(t, dm_bp)) - dirty_price

    try:
        dm = float(brentq(residual, -5000, 20000, maxiter=300, xtol=1e-10))
    except (ValueError, RuntimeError):
        dm = None
    analytics = {
        "clean_price": metric(clean_price, "pct_nominal", "market_clean_price", as_of, {}),
        "dirty_price": metric(dirty_price, "currency", "clean_plus_aci", as_of,
                              {"clean_price": clean_price, "aci": aci}),
        "discount_margin": metric(dm, "bp", "projected_floater_dm", as_of,
                                  {"curve": discount_curve.curve_id}, "CALCULATED" if dm is not None else "UNAVAILABLE"),
    }
    first = future[0]
    analytics["projected_next_coupon"] = metric(first.coupon, "currency", "reference_plus_margin", as_of,
                                                 {"reference_rate_pct": first.reference_rate,
                                                  "margin_bp": first.contractual_margin})
    analytics["current_coupon_rate"] = metric(first.coupon_rate, "pct", "reference_plus_margin", as_of,
                                               {"reference_rate_pct": first.reference_rate,
                                                "margin_bp": first.contractual_margin})
    if dm is not None:
        bump = 25.0
        # A curve shock also changes future coupons: approximate reset behaviour by shifting
        # projected coupon cash flows by the same parallel shock.
        def shocked_price(shift_bp: float) -> float:
            shifted_curve = discount_curve.shifted(shift_bp)
            shifted_flows = []
            for flow in future:
                accrual_proxy = flow.coupon / max(
                    (flow.coupon_rate or 0.0) / 100.0, 1e-12
                ) if flow.coupon else 0.0
                shifted_coupon = flow.coupon + accrual_proxy * shift_bp / 10000.0
                shifted_flows.append(CashFlow(**{**flow.__dict__, "coupon": max(0.0, shifted_coupon)}))
            return present_value(shifted_flows, as_of, lambda t: shifted_curve.discount_factor(t, dm))

        p_minus = shocked_price(-bump)
        p_plus = shocked_price(bump)
        effective = (p_minus - p_plus) / (2 * dirty_price * bump / 10000.0)
        spread_minus = present_value(future, as_of, lambda t: discount_curve.discount_factor(t, dm - bump))
        spread_plus = present_value(future, as_of, lambda t: discount_curve.discount_factor(t, dm + bump))
        spread_duration = (spread_minus - spread_plus) / (2 * dirty_price * bump / 10000.0)
        mv = market_value if market_value is not None else dirty_price
        analytics.update({
            "effective_duration": metric(effective, "years", "full_reprice_parallel_25bp", as_of, {"bump_bp": bump}),
            "spread_duration": metric(spread_duration, "years", "dm_reprice_25bp", as_of, {"bump_bp": bump}),
            "dv01": metric(effective * mv * 1e-4, "currency", "effective_duration_mv", as_of,
                           {"market_value": mv}),
        })
    return analytics
