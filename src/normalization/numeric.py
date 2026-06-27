from __future__ import annotations

import math
import re
from typing import Any


NA_TOKENS = {"", "-", "—", "–", "n/a", "na", "нет данных", "null", "none"}


def normalize_numeric_value(
    raw_value: Any,
    unit_multiplier: float | int | None = 1,
    sign_convention: str = "as_reported",
) -> float | None:
    """Normalize reported numeric text and apply unit multiplier.

    Handles spaces, NBSP, comma/dot decimal separators, parentheses as negatives,
    and common dash/empty tokens. Returns None when the value is absent.
    """
    if raw_value is None:
        return None
    if isinstance(raw_value, (int, float)):
        if isinstance(raw_value, float) and math.isnan(raw_value):
            return None
        value = float(raw_value)
    else:
        s = str(raw_value).strip().lower()
        s = s.replace("\xa0", " ").replace("\u202f", " ").replace("−", "-")
        if s in NA_TOKENS:
            return None
        neg = False
        if s.startswith("(") and s.endswith(")"):
            neg = True
            s = s[1:-1]
        s = re.sub(r"(млрд|млн|тыс|руб|₽|rub|rur|million|thousand|billion)", "", s, flags=re.I)
        s = s.replace(" ", "")
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            s = s.replace(",", ".")
        try:
            value = float(s)
        except ValueError:
            return None
        if neg:
            value = -value

    mult = 1 if unit_multiplier in (None, "") else float(unit_multiplier)
    value *= mult
    if sign_convention == "positive":
        value = abs(value)
    elif sign_convention == "negative":
        value = -abs(value)
    return value

