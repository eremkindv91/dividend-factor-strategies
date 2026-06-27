from __future__ import annotations


def safe_ratio(num: float | None, den: float | None) -> tuple[float | None, str | None]:
    if num is None or den is None:
        return None, "missing_input"
    if den <= 0:
        return None, "non_positive_denominator"
    return num / den, None


def validate_non_negative(metric: str, value: float | None) -> str | None:
    if metric in {"revenue", "total_assets"} and value is not None and value < 0:
        return "negative_value_not_allowed"
    return None

