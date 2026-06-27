from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


SOURCE_PRIORITY = {
    "manual_override": 1,
    "official_ifrs_xlsx": 2,
    "official_ifrs_pdf_text": 3,
    "official_ifrs_pdf_table": 4,
    "official_ifrs_ocr_high_confidence": 5,
    "issuer_website": 6,
    "smartlab": 7,
    "official_ifrs_ocr_low_confidence": 8,
    "llm_candidate_only": 9,
    "calculated_from_lower_priority_sources": 10,
}


@dataclass(frozen=True)
class SourceCandidate:
    value: float | int | None
    source_name: str
    source_type: str
    quality_score: float = 0
    confidence_score: float = 0
    source_url: str | None = None
    source_priority: int | None = None
    extraction_method: str | None = None
    period: str | None = None
    currency: str | None = None
    unit_type: str | None = None
    unit_multiplier: float | int | None = None

    @classmethod
    def from_mapping(cls, row: dict) -> "SourceCandidate":
        return cls(
            value=row.get("value_normalized", row.get("best_value")),
            source_name=str(row.get("source_name") or row.get("best_source_name") or ""),
            source_type=str(row.get("source_type") or row.get("best_source_type") or ""),
            quality_score=float(row.get("quality_score") or row.get("best_quality_score") or 0),
            confidence_score=float(row.get("confidence_score") or row.get("best_confidence_score") or 0),
            source_url=row.get("source_url") or row.get("best_source_url"),
            source_priority=int(row.get("source_priority") or SOURCE_PRIORITY.get(str(row.get("source_name") or ""), 99)),
            extraction_method=row.get("extraction_method"),
            period=None if row.get("period") is None else str(row.get("period")),
            currency=row.get("currency"),
            unit_type=row.get("unit_type"),
            unit_multiplier=row.get("unit_multiplier"),
        )


def relative_diff(a: float, b: float) -> float:
    return abs(a - b) / max(abs(b), 1.0)


def resolve_best_value(candidates: Iterable[SourceCandidate]) -> dict:
    rows = [c for c in candidates if c.value is not None]
    if not rows:
        return {
            "best": None,
            "selected_reason": "no_candidates",
            "conflict_flag": False,
            "conflict_type": None,
            "needs_manual_review": True,
        }

    manual = [c for c in rows if c.source_name == "manual_override" or c.source_type == "manual_override"]
    if manual:
        best = sorted(manual, key=lambda c: (-c.quality_score, c.source_priority or 99))[0]
        return _resolved(best, "manual_override", False, None, False)

    smart = [c for c in rows if c.source_name == "smartlab" or c.source_type == "aggregator"]
    official = [c for c in rows if c.source_name.startswith("official_ifrs") or c.source_type == "official_ifrs"]
    ocr = [c for c in official if "ocr" in (c.extraction_method or c.source_name)]

    low_ocr = [c for c in ocr if c.confidence_score < 0.85]
    if low_ocr and smart:
        best = sorted(smart, key=lambda c: (-c.quality_score, c.source_priority or 99))[0]
        return _resolved(best, "ocr_low_confidence_keep_smartlab", True, "ocr_low_confidence", True)

    high_official = [c for c in official if c.quality_score >= 80]
    if high_official and smart:
        best_off = sorted(high_official, key=lambda c: (-c.quality_score, c.source_priority or 99))[0]
        best_smart = sorted(smart, key=lambda c: (-c.quality_score, c.source_priority or 99))[0]
        diff = relative_diff(float(best_off.value), float(best_smart.value))
        if diff <= 0.01:
            return _resolved(best_off, "official_ifrs_confirmed_by_smartlab", False, None, False)
        if diff <= 0.05:
            return _resolved(best_off, "official_ifrs_warning_diff_1_5pct", True, "minor_source_diff", False)
        return _resolved(best_smart, "conflict_gt_5pct_keep_smartlab_until_review", True, "value_conflict_gt_5pct", True)

    viable = [c for c in rows if c.quality_score >= 70 and c.source_url]
    pool = viable or rows
    best = sorted(pool, key=lambda c: (c.source_priority or 99, -c.quality_score, -c.confidence_score))[0]
    reliable = bool(best.source_url and best.quality_score >= 70)
    return _resolved(best, "highest_priority_quality_source", False, None, not reliable)


def _resolved(best: SourceCandidate, reason: str, conflict: bool, conflict_type: str | None, review: bool) -> dict:
    return {
        "best": best,
        "selected_reason": reason,
        "conflict_flag": conflict,
        "conflict_type": conflict_type,
        "needs_manual_review": review,
    }
