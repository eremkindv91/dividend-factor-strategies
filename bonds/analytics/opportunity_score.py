"""Config-driven cross-structure opportunity score built from within-class percentiles."""
from __future__ import annotations

import math

from .relative_value import robust_percentile


def _num(value) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def score_opportunities(rows: list[dict], weights: dict[str, float]) -> None:
    expected = {"relative_value", "carry", "credit_quality", "liquidity", "rate_risk", "structure_risk", "data_quality"}
    if set(weights) != expected or abs(sum(float(v) for v in weights.values()) - 1.0) > 1e-9:
        raise ValueError("opportunity score weights must contain all dimensions and sum to 1")
    for row in rows:
        cls = row.get("structure_class")
        peers = [item for item in rows if item.get("structure_class") == cls]
        raw = {
            "relative_value": _num((row.get("relative_value") or {}).get("percentile")),
            "carry": robust_percentile([_num(x.get("carry_pct")) for x in peers], _num(row.get("carry_pct"))) if _num(row.get("carry_pct")) is not None else None,
            "credit_quality": _num(row.get("credit_quality_score")),
            "liquidity": _num(row.get("liquidity_score")),
            "rate_risk": _num(row.get("rate_risk_penalty")),
            "structure_risk": _num(row.get("structure_risk_penalty")),
            "data_quality": _num(row.get("data_quality_penalty")),
        }
        available_weight = sum(float(weights[k]) for k, value in raw.items() if value is not None)
        if available_weight <= 0:
            row["opportunity_score"] = None
            row["opportunity_score_decomposition"] = {"status": "UNAVAILABLE", "factors": raw}
            continue
        positive = {"relative_value", "carry", "credit_quality", "liquidity"}
        total = sum(
            float(weights[key]) * (value if key in positive else 100.0 - value)
            for key, value in raw.items() if value is not None
        ) / available_weight
        total = min(100.0, max(0.0, total))
        row["opportunity_score"] = round(total, 4)
        row["opportunity_score_decomposition"] = {
            "status": "CALCULATED", "factors": raw, "weights": weights,
            "available_weight": available_weight,
        }
