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
    ordered_findings = [
        _canonical_entity(raw)
        for output in sorted(outputs, key=lambda item: item.analyst)
        for raw in sorted(output.findings, key=lambda item: item.id)
    ]
    id_counts: dict[str, int] = defaultdict(int)
    for finding in ordered_findings:
        id_counts[finding.id] += 1
    ambiguous_ids = {finding_id for finding_id, count in id_counts.items() if count > 1}

    findings: list[Finding] = []
    seen_signatures: set[tuple[str, ...]] = set()
    removed = 0
    rejected: dict[str, list[str]] = {
        finding_id: ["duplicate_finding_id"] for finding_id in sorted(ambiguous_ids)
    }

    for finding in ordered_findings:
        if finding.id in ambiguous_ids:
            continue
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
