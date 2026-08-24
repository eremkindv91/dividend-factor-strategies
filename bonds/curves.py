"""Curve provider with interpolation, discount factors and freshness metadata."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class CurvePoint:
    tenor_years: float
    rate_pct: float


class CurveProvider:
    def __init__(self, points: Iterable[CurvePoint | tuple[float, float]], *, as_of: date,
                 source: str, curve_id: str = "OFZ_KBD") -> None:
        clean = sorted((
            CurvePoint(float(p.tenor_years), float(p.rate_pct)) if isinstance(p, CurvePoint)
            else CurvePoint(float(p[0]), float(p[1]))
            for p in points
        ), key=lambda point: point.tenor_years)
        if len(clean) < 2 or any(p.tenor_years <= 0 or not math.isfinite(p.rate_pct) for p in clean):
            raise ValueError("curve requires at least two positive finite tenor points")
        self.points = tuple(clean)
        self.as_of = as_of
        self.source = source
        self.curve_id = curve_id

    def rate_pct(self, tenor_years: float) -> float:
        tenor = max(float(tenor_years), 1e-9)
        x = np.array([p.tenor_years for p in self.points], dtype=float)
        y = np.array([p.rate_pct for p in self.points], dtype=float)
        return float(np.interp(tenor, x, y, left=y[0], right=y[-1]))

    def discount_factor(self, tenor_years: float, spread_bp: float = 0.0) -> float:
        tenor = max(float(tenor_years), 0.0)
        rate = (self.rate_pct(tenor) + float(spread_bp) / 100.0) / 100.0
        if rate <= -1:
            raise ValueError("discount rate must exceed -100%")
        return (1.0 + rate) ** (-tenor)

    def shifted(self, shift_bp: float) -> "CurveProvider":
        return CurveProvider(
            [(p.tenor_years, p.rate_pct + float(shift_bp) / 100.0) for p in self.points],
            as_of=self.as_of, source=self.source, curve_id=f"{self.curve_id}_SHIFT_{shift_bp:g}BP",
        )

    def metadata(self) -> dict:
        return {
            "curve_id": self.curve_id,
            "as_of": self.as_of.isoformat(),
            "source": self.source,
            "points": [{"tenor_years": p.tenor_years, "rate_pct": p.rate_pct} for p in self.points],
        }
