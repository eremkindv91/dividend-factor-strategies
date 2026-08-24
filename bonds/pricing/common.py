"""Shared numerical routines and provenance helpers."""
from __future__ import annotations

from datetime import date
import math
from typing import Callable, Iterable

from scipy.optimize import brentq

from bonds.cashflows import CashFlow, normalized_flows, year_fraction


def metric(value, unit: str, method: str, as_of: date, inputs: dict, status: str = "CALCULATED") -> dict:
    if isinstance(value, float) and not math.isfinite(value):
        value, status = None, "UNAVAILABLE"
    return {
        "value": value,
        "unit": unit,
        "method": method,
        "as_of": as_of.isoformat(),
        "status": status,
        "inputs": inputs,
    }


def solve_yield(flows: Iterable[CashFlow], dirty_price: float, as_of: date,
                lower: float = -0.95, upper: float = 5.0) -> float | None:
    future = normalized_flows(flows, as_of)
    if dirty_price <= 0 or not future:
        return None

    def residual(rate: float) -> float:
        return sum(flow.amount / (1 + rate) ** year_fraction(as_of, flow.date) for flow in future) - dirty_price

    try:
        return float(brentq(residual, lower, upper, maxiter=300, xtol=1e-12))
    except (ValueError, RuntimeError):
        return None


def present_value(flows: Iterable[CashFlow], as_of: date, discount: Callable[[float], float]) -> float:
    return sum(flow.amount * discount(year_fraction(as_of, flow.date)) for flow in normalized_flows(flows, as_of))
