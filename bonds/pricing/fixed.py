"""Fixed deterministic cash-flow analytics."""
from __future__ import annotations

from datetime import date
import math

from scipy.optimize import brentq

from bonds.cashflows import CashFlow, normalized_flows, year_fraction
from bonds.curves import CurveProvider
from .common import metric, present_value, solve_yield


def calculate_fixed(*, flows: list[CashFlow], dirty_price: float, clean_price: float,
                    aci: float, as_of: date, curve: CurveProvider | None = None,
                    market_value: float | None = None) -> dict:
    future = normalized_flows(flows, as_of)
    ytm = solve_yield(future, dirty_price, as_of)
    analytics: dict[str, dict] = {
        "clean_price": metric(clean_price, "pct_nominal", "market_clean_price", as_of, {}),
        "dirty_price": metric(dirty_price, "currency", "clean_plus_aci", as_of,
                              {"clean_price": clean_price, "aci": aci}),
        "aci": metric(aci, "currency", "market_aci", as_of, {}),
    }
    if ytm is None:
        analytics["ytm_gross"] = metric(None, "pct", "effective_annual_irr", as_of, {}, "UNAVAILABLE")
        return analytics
    pv_terms = [
        (year_fraction(as_of, flow.date), flow.amount / (1 + ytm) ** year_fraction(as_of, flow.date))
        for flow in future
    ]
    macaulay = sum(t * pv for t, pv in pv_terms) / dirty_price
    modified = sum(t * flow.amount / (1 + ytm) ** (t + 1) for t, flow in
                   [(year_fraction(as_of, f.date), f) for f in future]) / dirty_price
    bump = 1e-4
    p_minus = sum(f.amount / (1 + ytm - bump) ** year_fraction(as_of, f.date) for f in future)
    p_plus = sum(f.amount / (1 + ytm + bump) ** year_fraction(as_of, f.date) for f in future)
    convexity = (p_minus + p_plus - 2 * dirty_price) / (dirty_price * bump * bump)
    mv = dirty_price if market_value is None else market_value
    analytics.update({
        "ytm_gross": metric(ytm * 100, "pct", "effective_annual_irr", as_of, {"dirty_price": dirty_price}),
        "macaulay_duration": metric(macaulay, "years", "pv_weighted_time", as_of, {"ytm": ytm}),
        "modified_duration": metric(modified, "years", "effective_annual_analytical", as_of, {"ytm": ytm}),
        "dv01": metric(modified * mv * 1e-4, "currency", "modified_duration_mv", as_of,
                       {"market_value": mv, "modified_duration": modified}),
        "convexity": metric(convexity, "years2", "central_difference_1bp", as_of, {"bump": bump}),
    })
    if curve:
        maturity = max(year_fraction(as_of, f.date) for f in future)
        g_spread_bp = (ytm * 100 - curve.rate_pct(maturity)) * 100
        analytics["g_spread"] = metric(g_spread_bp, "bp", "ytm_minus_interpolated_government", as_of,
                                       {"tenor_years": maturity, "curve": curve.curve_id})

        def z_residual(spread_bp: float) -> float:
            return present_value(future, as_of, lambda t: curve.discount_factor(t, spread_bp)) - dirty_price

        try:
            z = float(brentq(z_residual, -5000, 20000, maxiter=300, xtol=1e-10))
            reverse = present_value(future, as_of, lambda t: curve.discount_factor(t, z))
            analytics["z_spread"] = metric(z, "bp", "government_curve_z_spread", as_of,
                                           {"curve": curve.curve_id, "reverse_price": reverse})
        except (ValueError, RuntimeError):
            analytics["z_spread"] = metric(None, "bp", "government_curve_z_spread", as_of, {}, "UNAVAILABLE")
    return analytics
