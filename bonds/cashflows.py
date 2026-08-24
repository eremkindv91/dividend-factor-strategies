"""Unified cash-flow representation shared by all v4 valuation engines."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable


@dataclass(frozen=True)
class CashFlow:
    date: date
    cashflow_type: str
    coupon: float = 0.0
    principal: float = 0.0
    reference_rate: float | None = None
    contractual_margin: float | None = None
    coupon_rate: float | None = None
    scenario: str = "base"
    source: str = "unknown"
    model_flag: str = "contractual"

    @property
    def amount(self) -> float:
        return float(self.coupon) + float(self.principal)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["date"] = self.date.isoformat()
        payload["amount"] = self.amount
        return payload


def year_fraction(start: date, end: date, convention: str = "ACT/365") -> float:
    if convention != "ACT/365":
        raise ValueError(f"unsupported day-count convention: {convention}")
    return (end - start).days / 365.0


def normalized_flows(flows: Iterable[CashFlow], as_of: date) -> list[CashFlow]:
    return sorted(
        (flow for flow in flows if flow.date > as_of and flow.amount > 0),
        key=lambda flow: (flow.date, flow.cashflow_type),
    )


def aggregate_same_date(flows: Iterable[CashFlow]) -> list[CashFlow]:
    grouped: dict[tuple, dict] = {}
    for flow in flows:
        key = (flow.date, flow.scenario, flow.source, flow.model_flag)
        item = grouped.setdefault(key, {
            "coupon": 0.0, "principal": 0.0, "reference_rate": flow.reference_rate,
            "contractual_margin": flow.contractual_margin, "coupon_rate": flow.coupon_rate,
        })
        item["coupon"] += flow.coupon
        item["principal"] += flow.principal
    return [
        CashFlow(
            date=key[0], cashflow_type="coupon_principal" if value["coupon"] and value["principal"]
            else "coupon" if value["coupon"] else "principal",
            coupon=value["coupon"], principal=value["principal"],
            reference_rate=value["reference_rate"], contractual_margin=value["contractual_margin"],
            coupon_rate=value["coupon_rate"], scenario=key[1], source=key[2], model_flag=key[3],
        )
        for key, value in sorted(grouped.items())
    ]
