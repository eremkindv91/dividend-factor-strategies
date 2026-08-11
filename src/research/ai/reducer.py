from __future__ import annotations

import re
from collections import defaultdict

from .schemas import AnalystOutput, Finding, ReducedFindings


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _canonical_entity(finding: Finding) -> Finding:
    entity = finding.entity_id.strip()
    if finding.entity_type in {"stock", "bank", "bond"}:
        entity = entity.upper()
    return finding.model_copy(update={"entity_id": entity})


def reduce_findings(outputs: list[AnalystOutput]) -> ReducedFindings:
    findings: list[Finding] = []
    seen_signatures: set[tuple[str, ...]] = set()
    seen_ids: dict[str, str] = {}
    removed = 0
    rejected: dict[str, list[str]] = {}

    ordered = sorted(outputs, key=lambda output: output.analyst)
    for output in ordered:
        for raw in sorted(output.findings, key=lambda finding: finding.id):
            finding = _canonical_entity(raw)
            signature = (
                finding.agent,
                finding.entity_type,
                finding.entity_id,
                finding.claim_type,
                _normalize_text(finding.claim),
            )
            if signature in seen_signatures:
                removed += 1
                continue
            if finding.id in seen_ids and seen_ids[finding.id] != _normalize_text(finding.claim):
                rejected[finding.id] = ["duplicate_finding_id_with_different_claim"]
                continue
            seen_ids[finding.id] = _normalize_text(finding.claim)
            seen_signatures.add(signature)
            findings.append(finding)

    groups: dict[tuple[str, str, str], list[Finding]] = defaultdict(list)
    for finding in findings:
        groups[(finding.entity_type, finding.entity_id, finding.claim_type)].append(finding)
    conflicts = {
        "|".join(key): [finding.id for finding in values]
        for key, values in sorted(groups.items())
        if len({_normalize_text(finding.claim) for finding in values}) > 1
    }
    return ReducedFindings(
        findings=sorted(findings, key=lambda finding: (finding.entity_type, finding.entity_id, finding.id)),
        exact_duplicates_removed=removed,
        conflicts=conflicts,
        rejected_before_verifier=rejected,
    )
