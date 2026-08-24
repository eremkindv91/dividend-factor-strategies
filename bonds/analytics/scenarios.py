"""Transparent rate/spread Total Return and breakeven scenarios."""
from __future__ import annotations

from scipy.optimize import brentq


def total_return(*, current_dirty: float, future_dirty: float, cashflows_received: float,
                 costs: float = 0.0) -> float:
    if current_dirty <= 0:
        raise ValueError("current dirty price must be positive")
    return (future_dirty + cashflows_received - current_dirty - costs) / current_dirty


def breakeven_shock(price_function, *, current_dirty: float, cashflows_received: float,
                    costs: float = 0.0, lower_bp: float = -5000, upper_bp: float = 5000) -> float | None:
    def objective(shock_bp: float) -> float:
        return total_return(
            current_dirty=current_dirty,
            future_dirty=float(price_function(shock_bp)),
            cashflows_received=cashflows_received,
            costs=costs,
        )
    try:
        return float(brentq(objective, lower_bp, upper_bp, maxiter=250))
    except (ValueError, RuntimeError):
        return None
