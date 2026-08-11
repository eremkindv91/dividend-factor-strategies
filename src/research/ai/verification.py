from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..freshness import parse_timestamp
from ..validators import validate_safe_content
from .schemas import Finding, ReducedFindings, VerificationDecision, VerifierOutput


SOURCE_REF = re.compile(r"^(?P<artifact>[A-Za-z0-9_./-]+\.json)#(?P<path>[A-Za-z0-9_.\[\]-]+)$")
PATH_PART = re.compile(r"(?P<key>[^.\[\]]+)|\[(?P<index>\d+)\]")
TARGET_PRICE = re.compile(r"\b(?:целевая|таргет|target)\s+(?:цена|price)\b", re.IGNORECASE)
UNSUPPORTED_FORECAST_TYPES = {"target_price", "generated_forecast", "price_forecast", "financial_projection"}


@dataclass(frozen=True)
class Preverification:
    findings: list[Finding]
    rejected: dict[str, list[str]]
    forced_partial: dict[str, list[str]]


def resolve_source_ref(source_ref: str, artifacts: dict[str, Any]) -> Any:
    match = SOURCE_REF.match(source_ref)
    if not match:
        raise KeyError("invalid_source_ref")
    current: Any = artifacts[match.group("artifact")]
    for part in PATH_PART.finditer(match.group("path")):
        if part.group("key") is not None:
            if not isinstance(current, dict):
                raise KeyError("source_path_not_object")
            current = current[part.group("key")]
        else:
            if not isinstance(current, list):
                raise KeyError("source_path_not_list")
            current = current[int(part.group("index"))]
    return current


def _same_value(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isfinite(float(expected)) and math.isfinite(float(actual)) and math.isclose(
            float(expected), float(actual), rel_tol=1e-9, abs_tol=1e-9
        )
    return expected == actual


def _pit_partial(finding: Finding) -> bool:
    return any(
        evidence.source_ref.startswith(("fundamentals_snapshot.json#", "stocks/"))
        and any(token in evidence.source_ref for token in ("fundamentals", "quality.publication", "point_in_time"))
        for evidence in finding.evidence
    )


def preverify_findings(
    reduced: ReducedFindings,
    artifacts: dict[str, Any],
    *,
    now: datetime | None = None,
) -> Preverification:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    accepted: list[Finding] = []
    rejected = dict(reduced.rejected_before_verifier)
    partial: dict[str, list[str]] = {}

    for finding in reduced.findings:
        reasons: list[str] = []
        safety = validate_safe_content(f"finding:{finding.id}", finding.model_dump(mode="json"), now=current)
        reasons.extend(safety.errors)
        if finding.materiality == "high" and not finding.evidence:
            reasons.append("high_materiality_without_evidence")
        if finding.causal_claim and (len(finding.evidence) < 2 or not finding.counter_evidence):
            reasons.append("causal_claim_without_sufficient_evidence_and_counter_evidence")
        if finding.claim_type in {"model_signal", "tradable_signal"} and finding.entity_type in {"sector", "stock"}:
            reasons.append("ai_layer_cannot_promote_model_signal")
        if "я рассчитал" in finding.claim.casefold() or "i calculated" in finding.claim.casefold():
            reasons.append("ai_financial_calculation_claim_forbidden")
        if TARGET_PRICE.search(finding.claim):
            reasons.append("target_price_claim_forbidden")
        if finding.claim_type.casefold() in UNSUPPORTED_FORECAST_TYPES:
            reasons.append("unsupported_forecast_claim")

        for evidence in finding.evidence + finding.counter_evidence:
            try:
                actual = resolve_source_ref(evidence.source_ref, artifacts)
            except (KeyError, IndexError, TypeError):
                reasons.append(f"unsupported_source_ref:{evidence.source_ref}")
                continue
            if not _same_value(evidence.value, actual):
                reasons.append(f"evidence_value_mismatch:{evidence.source_ref}")
            stamp = parse_timestamp(evidence.asof)
            if stamp and stamp > current:
                reasons.append(f"future_evidence_timestamp:{evidence.source_ref}")

        if reasons:
            rejected[finding.id] = sorted(set(reasons))
            continue
        updated = finding
        if _pit_partial(finding):
            warning = "Publication timestamp unavailable; point-in-time lineage is partial."
            partial[finding.id] = [warning]
            updated = finding.model_copy(
                update={
                    "confidence": min(finding.confidence, 0.6),
                    "warnings": sorted(set(finding.warnings + [warning])),
                }
            )
        accepted.append(updated)
    return Preverification(findings=accepted, rejected=rejected, forced_partial=partial)


def validate_verifier_output(
    verifier: VerifierOutput,
    findings: list[Finding],
    forced_partial: dict[str, list[str]],
) -> VerifierOutput:
    expected = {finding.id: finding for finding in findings}
    decisions = {decision.finding_id: decision for decision in verifier.decisions}
    if set(decisions) != set(expected):
        raise ValueError("verifier decisions must match input finding IDs exactly")
    normalized: list[VerificationDecision] = []
    for finding_id in sorted(expected):
        finding = expected[finding_id]
        decision = decisions[finding_id]
        status = "PARTIAL" if finding_id in forced_partial and decision.status == "PASS" else decision.status
        confidence = min(decision.adjusted_confidence, finding.confidence)
        warnings = sorted(set(decision.warnings + forced_partial.get(finding_id, [])))
        normalized.append(
            decision.model_copy(
                update={"status": status, "adjusted_confidence": confidence, "warnings": warnings}
            )
        )
    return verifier.model_copy(update={"decisions": normalized})


def split_verified(
    findings: list[Finding], verifier: VerifierOutput
) -> tuple[list[Finding], list[Finding], dict[str, list[str]]]:
    by_id = {finding.id: finding for finding in findings}
    passed: list[Finding] = []
    partial: list[Finding] = []
    rejected: dict[str, list[str]] = {}
    for decision in verifier.decisions:
        finding = by_id[decision.finding_id]
        updated = finding.model_copy(
            update={
                "confidence": decision.adjusted_confidence,
                "warnings": sorted(set(finding.warnings + decision.warnings)),
            }
        )
        if decision.status == "PASS":
            passed.append(updated)
        elif decision.status == "PARTIAL":
            partial.append(updated)
        else:
            rejected[finding.id] = decision.reasons
    return passed, partial, rejected
