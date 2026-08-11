from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .schemas import (
    AnalystOutput,
    Evidence,
    Finding,
    MarketMemo,
    MemoSection,
    RegimeView,
    StockMemo,
    VerificationDecision,
    VerifierOutput,
)


Agent = Literal["market", "macro", "equity", "bonds", "banks", "news", "stock"]
EntityType = Literal["market", "macro", "sector", "stock", "bank", "bond", "news"]
FindingKind = Literal["fact", "inference", "hypothesis"]
Materiality = Literal["low", "medium", "high"]
Verdict = Literal["PASS", "PARTIAL", "REJECT"]
ConfidenceLabel = Literal["low", "medium", "high"]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WireFinding(WireModel):
    id: str
    claim: str
    entity_type: EntityType
    entity_id: str
    claim_type: str
    kind: FindingKind
    materiality: Materiality
    confidence: float
    causal: bool
    evidence_refs: list[str]
    counter_evidence_refs: list[str]
    warnings: list[str]
    invalidation: list[str]


class WireAnalystOutput(WireModel):
    findings: list[WireFinding]
    warnings: list[str]


class WireVerificationDecision(WireModel):
    finding_id: str
    verdict: Verdict
    confidence: float
    reason: str
    warnings: list[str]


class WireVerifierOutput(WireModel):
    results: list[WireVerificationDecision]
    warnings: list[str]


MarketSection = Literal[
    "sector_context",
    "equity_context",
    "bond_context",
    "bank_context",
    "catalysts",
    "contradictions",
    "risks",
    "watch",
]
StockSection = Literal[
    "investment_view",
    "market_context",
    "sector_context",
    "company_vs_sector",
    "valuation",
    "quality",
    "dividends",
    "momentum_positioning",
    "catalysts",
    "risks",
    "contradictions",
]


class WireMarketSection(WireModel):
    key: MarketSection
    summary: str
    finding_ids: list[str]


class WireStockSection(WireModel):
    key: StockSection
    summary: str
    finding_ids: list[str]


class WireMarketMemo(WireModel):
    regime_label: str
    regime_confidence: ConfidenceLabel
    summary: str
    key_finding_ids: list[str]
    sections: list[WireMarketSection]


class WireStockMemo(WireModel):
    confidence: ConfidenceLabel
    sections: list[WireStockSection]
    invalidation: list[str]


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    metric: str
    value: str | int | float | bool | None
    asof: str | None
    source_ref: str

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "metric": self.metric,
            "value": self.value,
            "asof": self.asof,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True)
class EvidenceCatalog:
    entries: tuple[CatalogEntry, ...]

    @property
    def by_id(self) -> dict[str, CatalogEntry]:
        return {entry.id: entry for entry in self.entries}

    def public(self) -> list[dict[str, Any]]:
        return [entry.public() for entry in self.entries]


_SAFE_PATH_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _scalar_leaves(value: Any, path: str = ""):
    if value is None or isinstance(value, (str, int, float, bool)):
        yield path, value
        return
    if isinstance(value, dict):
        for key in sorted(value):
            if not _SAFE_PATH_KEY.fullmatch(str(key)):
                continue
            child = f"{path}.{key}" if path else str(key)
            yield from _scalar_leaves(value[key], child)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _scalar_leaves(item, f"{path}[{index}]")


def build_evidence_catalog(artifacts: dict[str, dict[str, Any]]) -> EvidenceCatalog:
    rows: list[tuple[str, str | int | float | bool | None, str | None]] = []
    for artifact_name in sorted(artifacts):
        artifact = artifacts[artifact_name]
        default_asof = artifact.get("asof") if isinstance(artifact.get("asof"), str) else None
        for path, value in _scalar_leaves(artifact):
            if not path:
                continue
            source_ref = f"{artifact_name}#{path}"
            asof = value if path.endswith("asof") and isinstance(value, str) else default_asof
            rows.append((source_ref, value, asof))
    entries = tuple(
        CatalogEntry(
            id=f"E{index:04d}",
            metric=source_ref.split("#", 1)[1].rsplit(".", 1)[-1].split("[")[0],
            value=value,
            asof=asof,
            source_ref=source_ref,
        )
        for index, (source_ref, value, asof) in enumerate(rows, start=1)
    )
    return EvidenceCatalog(entries)


def _hydrate_refs(refs: list[str], catalog: EvidenceCatalog) -> list[Evidence]:
    by_id = catalog.by_id
    unknown = sorted(set(refs) - set(by_id))
    if unknown:
        raise ValueError(f"unknown evidence refs: {', '.join(unknown)}")
    return [
        Evidence(
            metric=by_id[ref].metric,
            value=by_id[ref].value,
            asof=by_id[ref].asof,
            source_ref=by_id[ref].source_ref,
        )
        for ref in refs
    ]


def _domain_finding_id(agent: Agent, wire_id: str) -> str:
    raw = wire_id.strip()
    if not raw:
        raise ValueError("empty wire finding ID")
    candidate = f"{agent}:{raw}"
    if len(candidate) <= 120:
        return candidate
    digest = sha256(raw.encode("utf-8")).hexdigest()[:12]
    prefix = f"{agent}:"
    return f"{prefix}{raw[:120 - len(prefix) - len(digest) - 1]}:{digest}"


def hydrate_analyst_output(
    wire: WireAnalystOutput,
    *,
    agent: Agent,
    catalog: EvidenceCatalog,
) -> AnalystOutput:
    ids = [finding.id for finding in wire.findings]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate wire finding IDs")
    findings = [
        Finding(
            id=_domain_finding_id(agent, row.id),
            agent=agent,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            claim=row.claim,
            claim_type=row.claim_type,
            fact_inference_type=row.kind,
            evidence=_hydrate_refs(row.evidence_refs, catalog),
            counter_evidence=_hydrate_refs(row.counter_evidence_refs, catalog),
            materiality=row.materiality,
            confidence=row.confidence,
            causal_claim=row.causal,
            warnings=row.warnings,
            what_would_change_my_mind=row.invalidation,
        )
        for row in wire.findings
    ]
    return AnalystOutput(analyst=agent, findings=findings, warnings=wire.warnings)


def hydrate_verifier_output(wire: WireVerifierOutput) -> VerifierOutput:
    return VerifierOutput(
        decisions=[
            VerificationDecision(
                finding_id=row.finding_id,
                status=row.verdict,
                adjusted_confidence=row.confidence,
                reasons=[row.reason],
                warnings=row.warnings,
            )
            for row in wire.results
        ],
        global_warnings=wire.warnings,
    )


def _sections(rows: list[WireMarketSection] | list[WireStockSection], expected: set[str]) -> dict[str, MemoSection]:
    keys = [row.key for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate memo sections")
    if set(keys) != expected:
        raise ValueError(f"memo sections mismatch: expected {sorted(expected)}, got {sorted(keys)}")
    return {row.key: MemoSection(summary=row.summary, finding_ids=row.finding_ids) for row in rows}


MARKET_SECTIONS = {
    "sector_context", "equity_context", "bond_context", "bank_context",
    "catalysts", "contradictions", "risks", "watch",
}
STOCK_SECTIONS = {
    "investment_view", "market_context", "sector_context", "company_vs_sector", "valuation",
    "quality", "dividends", "momentum_positioning", "catalysts", "risks", "contradictions",
}


def hydrate_market_memo(
    wire: WireMarketMemo,
    *,
    asof: str,
    generated_at: str,
    status: Literal["complete", "partial"],
) -> MarketMemo:
    sections = _sections(wire.sections, MARKET_SECTIONS)
    return MarketMemo(
        asof=asof,
        generated_at=generated_at,
        status=status,
        regime=RegimeView(
            label=wire.regime_label,
            confidence=wire.regime_confidence,
            finding_ids=wire.key_finding_ids,
        ),
        summary=wire.summary,
        key_findings=wire.key_finding_ids,
        data_quality=[],
        verification_summary={},
        sources=[],
        evidence=[],
        **sections,
    )


def hydrate_stock_memo(
    wire: WireStockMemo,
    *,
    ticker: str,
    asof: str,
    generated_at: str,
    status: Literal["complete", "partial"],
) -> StockMemo:
    sections = _sections(wire.sections, STOCK_SECTIONS)
    return StockMemo(
        ticker=ticker,
        asof=asof,
        generated_at=generated_at,
        status=status,
        what_would_change_the_view=wire.invalidation,
        confidence=wire.confidence,
        evidence=[],
        data_quality=[],
        **sections,
    )


def schema_statistics(model: type[BaseModel], *, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema or model.model_json_schema()
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))

    def walk(value: Any, depth: int = 0) -> tuple[int, int, int, int, set[str]]:
        maximum = depth
        properties = required = enums = 0
        constructs: set[str] = set()
        if isinstance(value, dict):
            properties += len(value.get("properties") or {})
            required += len(value.get("required") or [])
            enums += 1 if "enum" in value else 0
            constructs.update(key for key in ("anyOf", "oneOf", "allOf", "$defs", "$ref", "additionalProperties") if key in value)
            for item in value.values():
                child = walk(item, depth + 1)
                maximum = max(maximum, child[0])
                properties += child[1]
                required += child[2]
                enums += child[3]
                constructs.update(child[4])
        elif isinstance(value, list):
            for item in value:
                child = walk(item, depth + 1)
                maximum = max(maximum, child[0])
                properties += child[1]
                required += child[2]
                enums += child[3]
                constructs.update(child[4])
        return maximum, properties, required, enums, constructs

    depth, properties, required, enums, constructs = walk(schema)
    return {
        "model": model.__name__,
        "bytes": len(encoded.encode("utf-8")),
        "max_depth": depth,
        "properties": properties,
        "required": required,
        "enums": enums,
        "constructs": sorted(constructs),
    }


def validate_wire_schema_compatibility(diagnostics: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    forbidden = {"anyOf", "oneOf", "allOf", "$defs", "$ref", "additionalProperties"}
    for row in diagnostics:
        name = row["model"]
        if row["bytes"] > 6_000:
            errors.append(f"{name}:schema_too_large:{row['bytes']}")
        if row["max_depth"] > 12:
            errors.append(f"{name}:schema_too_deep:{row['max_depth']}")
        unsupported = forbidden & set(row["constructs"])
        if unsupported:
            errors.append(f"{name}:unsupported_constructs:{','.join(sorted(unsupported))}")
    return errors
