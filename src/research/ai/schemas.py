from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Scalar = str | int | float | bool | None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Evidence(StrictModel):
    metric: str = Field(min_length=1, max_length=120)
    value: Scalar
    asof: str | None = None
    source_ref: str = Field(min_length=3, max_length=300)


class Finding(StrictModel):
    id: str = Field(min_length=3, max_length=120)
    agent: Literal["market", "macro", "equity", "bonds", "banks", "news", "stock"]
    entity_type: Literal["market", "macro", "sector", "stock", "bank", "bond", "news"]
    entity_id: str = Field(min_length=1, max_length=80)
    claim: str = Field(min_length=3, max_length=900)
    claim_type: str = Field(min_length=1, max_length=80)
    fact_inference_type: Literal["fact", "inference", "hypothesis"]
    evidence: list[Evidence] = Field(default_factory=list, max_length=12)
    counter_evidence: list[Evidence] = Field(default_factory=list, max_length=12)
    materiality: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0, le=1)
    causal_claim: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=12)
    what_would_change_my_mind: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def material_claim_requires_evidence(self) -> "Finding":
        if self.materiality == "high" and not self.evidence:
            raise ValueError("high-materiality finding requires evidence")
        return self


class AnalystOutput(StrictModel):
    analyst: Literal["market", "macro", "equity", "bonds", "banks", "news", "stock"]
    findings: list[Finding] = Field(default_factory=list, max_length=24)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class ReducedFindings(StrictModel):
    findings: list[Finding]
    exact_duplicates_removed: int = 0
    conflicts: dict[str, list[str]] = Field(default_factory=dict)
    rejected_before_verifier: dict[str, list[str]] = Field(default_factory=dict)


class VerificationDecision(StrictModel):
    finding_id: str
    status: Literal["PASS", "PARTIAL", "REJECT"]
    adjusted_confidence: float = Field(ge=0, le=1)
    reasons: list[str] = Field(min_length=1, max_length=12)
    warnings: list[str] = Field(default_factory=list, max_length=12)


class VerifierOutput(StrictModel):
    decisions: list[VerificationDecision]
    global_warnings: list[str] = Field(default_factory=list, max_length=20)


class MemoSection(StrictModel):
    summary: str = Field(default="", max_length=900)
    finding_ids: list[str] = Field(default_factory=list, max_length=24)


class RegimeView(StrictModel):
    label: str = Field(max_length=100)
    confidence: Literal["low", "medium", "high"]
    finding_ids: list[str] = Field(default_factory=list, max_length=12)


class MarketMemo(StrictModel):
    schema_version: int = 1
    asof: str
    generated_at: str
    status: Literal["complete", "partial"]
    regime: RegimeView
    summary: str = Field(max_length=1500)
    key_findings: list[str] = Field(default_factory=list)
    sector_context: MemoSection
    equity_context: MemoSection
    bond_context: MemoSection
    bank_context: MemoSection
    catalysts: MemoSection
    contradictions: MemoSection
    risks: MemoSection
    watch: MemoSection
    data_quality: list[str]
    verification_summary: dict[str, int]
    sources: list[str]
    evidence: list[Finding] = Field(default_factory=list)
    ai_run_fingerprint: str = ""
    publishable: bool = False


class StockMemo(StrictModel):
    schema_version: int = 1
    ticker: str
    asof: str
    generated_at: str
    status: Literal["complete", "partial"]
    investment_view: MemoSection
    market_context: MemoSection
    sector_context: MemoSection
    company_vs_sector: MemoSection
    valuation: MemoSection
    quality: MemoSection
    dividends: MemoSection
    momentum_positioning: MemoSection
    catalysts: MemoSection
    risks: MemoSection
    contradictions: MemoSection
    what_would_change_the_view: list[str]
    confidence: Literal["low", "medium", "high"]
    evidence: list[Finding] = Field(default_factory=list)
    data_quality: list[str]
    stock_ai_fingerprint: str = ""
    publishable: bool = False


class RequestUsage(StrictModel):
    node: str
    model: str
    attempts: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    duration_ms: int
    rate_limit_errors: int = 0


class NodeStatus(StrictModel):
    status: Literal["success", "failed", "cached", "skipped"]
    critical: bool
    error_type: str | None = None
    warning: str | None = None
    duration_ms: int = 0


class RunTelemetry(StrictModel):
    schema_version: int = 1
    run_id: str
    ai_run_fingerprint: str
    graph_version: str
    prompt_versions: dict[str, str]
    model_settings: dict[str, Any] = Field(
        validation_alias="model_config",
        serialization_alias="model_config",
    )
    started_at: str
    finished_at: str | None = None
    duration_ms: int = 0
    nodes: dict[str, NodeStatus] = Field(default_factory=dict)
    raw_findings: int = 0
    reduced_findings: int = 0
    verified_pass: int = 0
    verified_partial: int = 0
    verified_reject: int = 0
    compression_ratio: float | None = None
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    rate_limit_errors: int = 0
    retries: int = 0
    cache_hits: int = 0
    billing_allowed: bool = False
    estimated_cost: float | None = None
    publishable: bool = False
    warnings: list[str] = Field(default_factory=list)


class UniverseSelection(StrictModel):
    mode: Literal["priority", "changed", "explicit", "all"]
    eligible: list[str]
    selected: list[str]
    excluded: dict[str, list[str]]
    ranking_method: str
