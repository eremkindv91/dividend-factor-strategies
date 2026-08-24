"""Structure-aware peer benchmarks without cross-class yield comparisons."""
from __future__ import annotations

import math
from statistics import median


PRIMARY_METRIC = {
    "FIXED_BULLET": "z_spread_bp",
    "AMORTIZING_FIXED": "z_spread_bp",
    "PUTTABLE_FIXED": "yield_to_worst_pct",
    "CALLABLE_FIXED": "yield_to_worst_pct",
    "FLOATER": "discount_margin_bp",
    "PERPETUAL_RESET": "structural_premium_bp",
    "SUBORDINATED": "structural_premium_bp",
}


def _number(value) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def robust_percentile(values: list[float], value: float) -> float | None:
    clean = sorted(v for item in values if (v := _number(item)) is not None)
    if not clean:
        return None
    below = sum(item < value for item in clean)
    equal = sum(item == value for item in clean)
    return 100.0 * (below + 0.5 * equal) / len(clean)


def attach_relative_value(rows: list[dict], minimum_peers: int = 3) -> None:
    """Mutates derived rows only; peers are always from the same structure class."""
    for row in rows:
        cls = row.get("structure_class")
        metric_key = PRIMARY_METRIC.get(cls)
        own = _number(row.get(metric_key)) if metric_key else None
        peers = [
            item for item in rows
            if item is not row and item.get("structure_class") == cls
            and _number(item.get(metric_key)) is not None
            and (
                item.get("issuer_id") == row.get("issuer_id")
                or item.get("rating_group") == row.get("rating_group")
                or item.get("sector") == row.get("sector")
            )
        ] if metric_key else []
        values = [_number(item.get(metric_key)) for item in peers]
        values = [value for value in values if value is not None]
        if own is None or len(values) < minimum_peers:
            row["relative_value"] = {
                "metric": metric_key, "value": own, "peer_median": None,
                "excess": None, "percentile": None, "peer_n": len(values),
                "status": "INSUFFICIENT_PEERS",
            }
            continue
        benchmark = median(values)
        row["relative_value"] = {
            "metric": metric_key, "value": own, "peer_median": benchmark,
            "excess": own - benchmark, "percentile": robust_percentile(values + [own], own),
            "peer_n": len(values), "status": "CALCULATED",
        }
