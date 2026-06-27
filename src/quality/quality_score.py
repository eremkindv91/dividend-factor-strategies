from __future__ import annotations


def quality_bucket(score: float | int | None) -> str:
    if score is None:
        return "manual_review"
    score = float(score)
    if score >= 90:
        return "good"
    if score >= 70:
        return "acceptable"
    if score >= 50:
        return "partial"
    return "manual_review"

