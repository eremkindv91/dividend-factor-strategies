"""Deterministic yield-to-put/call scenarios without fake OAS."""
from __future__ import annotations

from datetime import date, datetime

from bonds.cashflows import CashFlow
from .common import metric, solve_yield


def _date(value: str) -> date:
    return datetime.fromisoformat(value[:10]).date()


def _exercise_yield(flows: list[CashFlow], dirty_price: float, as_of: date,
                    event: dict, scenario: str) -> float | None:
    event_date = _date(event["date"])
    price = float(event.get("price_pct", 100.0)) * float(event["face"]) / 100.0
    scenario_flows = [flow for flow in flows if as_of < flow.date <= event_date]
    existing = next((flow for flow in scenario_flows if flow.date == event_date), None)
    if existing:
        scenario_flows.remove(existing)
        scenario_flows.append(CashFlow(**{
            **existing.__dict__, "principal": existing.principal + price,
            "cashflow_type": "coupon_principal", "scenario": scenario,
        }))
    else:
        scenario_flows.append(CashFlow(event_date, "principal", principal=price, scenario=scenario,
                                       source=str(event.get("source") or "terms_registry")))
    return solve_yield(scenario_flows, dirty_price, as_of)


def calculate_optioned(*, flows: list[CashFlow], dirty_price: float, as_of: date,
                       put_schedule: list[dict], call_schedule: list[dict],
                       maturity_yield: float | None = None) -> dict:
    analytics: dict[str, dict] = {
        "oas": metric(None, "bp", "not_implemented_requires_stochastic_option_model", as_of, {}, "UNSUPPORTED")
    }
    applicable: list[float] = []
    for label, schedule in (("ytp", put_schedule), ("ytc_if_call", call_schedule)):
        values = []
        for event in schedule:
            value = _exercise_yield(flows, dirty_price, as_of, event, label)
            values.append({"date": event["date"], "yield_pct": value * 100 if value is not None else None})
            if value is not None:
                applicable.append(value)
        analytics[label] = metric(values, "pct", f"conditional_{label}_irr", as_of,
                                  {"dirty_price": dirty_price}, "CALCULATED" if values else "UNAVAILABLE")
    if maturity_yield is not None:
        applicable.append(maturity_yield)
    analytics["yield_to_worst"] = metric(
        min(applicable) * 100 if applicable else None, "pct", "minimum_applicable_scenario_yield",
        as_of, {}, "CALCULATED" if applicable else "UNAVAILABLE",
    )
    return analytics
