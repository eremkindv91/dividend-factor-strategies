from __future__ import annotations

from typing import Any, Literal, TypedDict

RESEARCH_SCHEMA_VERSION = 1

POINT_IN_TIME_QUALITIES = {"verified", "partial", "unknown"}
SURVIVORSHIP_STATUSES = {"controlled", "partial", "unknown"}
COMPONENT_STATUSES = {"available", "degraded", "unavailable"}

RESEARCH_ARTIFACTS = (
    "market_snapshot.json",
    "fundamentals_snapshot.json",
    "sector_snapshot.json",
    "stock_index.json",
    "ml_snapshot.json",
    "bank_snapshot.json",
    "bond_snapshot.json",
    "news_snapshot.json",
)

REQUIRED_MANIFEST_COMPONENTS = {
    "market",
    "fundamentals",
    "sectors",
    "stocks",
    "ml",
    "banks",
    "bonds",
    "news",
}


class QualityMetadata(TypedDict):
    fresh: bool
    age_days: float | None
    missing_fields: list[str]
    warnings: list[str]
    source_quality: str
    point_in_time_quality: Literal["verified", "partial", "unknown"]


class ComponentManifest(TypedDict):
    asof: str | None
    fresh: bool
    status: Literal["available", "degraded", "unavailable"]
    source_files: list[str]
    fingerprint: str
    warnings: list[str]


class ResearchManifest(TypedDict):
    schema_version: int
    research_asof: str | None
    research_asof_basis: str
    generated_at: str
    research_input_hash: str
    components: dict[str, ComponentManifest]
    component_date_span: dict[str, Any]
    survivorship_status: Literal["controlled", "partial", "unknown"]
    warnings: list[str]
    validation_errors: list[str]
    ready_for_ai: bool
    schema_ready: bool
    ai_input_ready: bool
    cross_domain_ready: bool
    temporal_warnings: list[str]
    component_eligibility: dict[str, dict[str, Any]]
