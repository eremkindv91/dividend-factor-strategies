"""Amortizing deterministic bond analytics and WAL."""
from __future__ import annotations

from datetime import date

from bonds.cashflows import CashFlow, normalized_flows, year_fraction
from bonds.curves import CurveProvider
from .common import metric
from .fixed import calculate_fixed


def calculate_amortizing(*, flows: list[CashFlow], dirty_price: float, clean_price: float,
                         aci: float, as_of: date, curve: CurveProvider | None = None,
                         market_value: float | None = None) -> dict:
    analytics = calculate_fixed(
        flows=flows, dirty_price=dirty_price, clean_price=clean_price, aci=aci,
        as_of=as_of, curve=curve, market_value=market_value,
    )
    principal = [(year_fraction(as_of, f.date), f.principal) for f in normalized_flows(flows, as_of) if f.principal > 0]
    total = sum(amount for _, amount in principal)
    wal = sum(t * amount for t, amount in principal) / total if total else None
    analytics["wal"] = metric(wal, "years", "principal_weighted_average_life", as_of,
                              {"principal_total": total}, "CALCULATED" if wal is not None else "UNAVAILABLE")
    return analytics
