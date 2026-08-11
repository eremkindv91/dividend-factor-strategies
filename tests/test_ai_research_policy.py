from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.research.ai.cache import ai_run_fingerprint, stock_ai_fingerprint
from src.research.ai.client import (
    CapacityError,
    GeminiClient,
    RequestBudget,
    TechnicalAIError,
    _gemini_json_schema,
    _safe_error_detail,
)
from src.research.ai.config import AIConfig
from src.research.ai.prompts import PROMPT_VERSIONS
from src.research.ai.reducer import reduce_findings
from src.research.ai.schemas import AnalystOutput, Evidence, Finding, VerifierOutput
from src.research.ai.verification import preverify_findings, split_verified, validate_verifier_output
from scripts.run_ai_research import ANALYST_FALLBACKS, _available_model


def _finding(
    finding_id: str = "market_fact",
    *,
    agent: str = "market",
    entity_type: str = "market",
    claim_type: str = "state",
    source_ref: str = "market_snapshot.json#asof",
    value="2026-08-10",
    asof: str = "2026-08-10",
) -> Finding:
    return Finding(
        id=finding_id,
        agent=agent,
        entity_type=entity_type,
        entity_id="IMOEX",
        claim="Проверяемое состояние рынка.",
        claim_type=claim_type,
        fact_inference_type="fact",
        evidence=[Evidence(metric="asof", value=value, asof=asof, source_ref=source_ref)],
        materiality="medium",
        confidence=0.8,
    )


def test_free_only_mode_rejects_billing_and_unverified_real_execution():
    with pytest.raises(ValidationError, match="free-only"):
        AIConfig(billing_allowed=True)
    with pytest.raises(ValidationError, match="AI_REAL_GEMINI_SMOKE_AUTHORIZED"):
        AIConfig(execution_mode="real", free_tier_verified=False)
    config = AIConfig(execution_mode="real", real_execution_authorized=True)
    assert config.billing_allowed is False
    assert config.free_tier_verified is False


def test_model_preflight_keeps_configured_model_when_available():
    selected, used_fallback = _available_model(
        "gemini-3.1-flash-lite",
        {"gemini-3.1-flash-lite", "gemini-2.5-flash-lite"},
        ANALYST_FALLBACKS,
    )
    assert selected == "gemini-3.1-flash-lite"
    assert used_fallback is False


def test_model_preflight_uses_only_allowlisted_flash_fallback():
    selected, used_fallback = _available_model(
        "gemini-unavailable",
        {"gemini-paid-pro", "gemini-2.5-flash-lite"},
        ANALYST_FALLBACKS,
    )
    assert selected == "gemini-2.5-flash-lite"
    assert used_fallback is True

    with pytest.raises(RuntimeError, match="no allowlisted Flash fallback"):
        _available_model("gemini-unavailable", {"gemini-paid-pro"}, ANALYST_FALLBACKS)


def test_provider_error_diagnostics_redact_gemini_key(monkeypatch):
    key = "AI" + "za012345678901234567890123456789"
    monkeypatch.setenv("GEMINI_API_KEY", key)
    detail = _safe_error_detail(RuntimeError(f"request failed for key {key}"))
    assert key not in detail
    assert "<redacted>" in detail


def test_fingerprint_changes_on_prompt_model_and_graph_change(monkeypatch):
    manifest = {
        "schema_version": 1,
        "research_input_hash": "sha256:research",
        "components": {
            "market": {"fingerprint": "sha256:market"},
            "sectors": {"fingerprint": "sha256:sector"},
            "news": {"fingerprint": "sha256:news"},
        },
    }
    base = AIConfig()
    base_run = ai_run_fingerprint(manifest, base)
    base_stock = stock_ai_fingerprint(stock_fingerprint="sha256:stock", manifest=manifest, config=base)
    assert ai_run_fingerprint(manifest, base.model_copy(update={"analyst_model": "another"})) != base_run
    assert ai_run_fingerprint(manifest, base.model_copy(update={"graph_version": "graph_v2"})) != base_run
    assert ai_run_fingerprint(
        manifest,
        AIConfig(execution_mode="real", real_execution_authorized=True),
    ) != base_run
    monkeypatch.setitem(PROMPT_VERSIONS, "market", "market_analyst_v2")
    assert ai_run_fingerprint(manifest, base) != base_run
    assert stock_ai_fingerprint(stock_fingerprint="sha256:stock", manifest=manifest, config=base) == base_stock
    monkeypatch.setitem(PROMPT_VERSIONS, "stock", "stock_analyst_v2")
    assert stock_ai_fingerprint(stock_fingerprint="sha256:stock", manifest=manifest, config=base) != base_stock


def test_sector_research_only_claim_cannot_be_promoted_to_signal():
    finding = _finding(
        "sector_signal",
        agent="equity",
        entity_type="sector",
        claim_type="tradable_signal",
        source_ref="sector_snapshot.json#asof",
    )
    reduced = reduce_findings([AnalystOutput(analyst="equity", findings=[finding])])
    result = preverify_findings(
        reduced,
        {"sector_snapshot.json": {"asof": "2026-08-10"}},
    )
    assert result.findings == []
    assert "ai_layer_cannot_promote_model_signal" in result.rejected["sector_signal"]


@pytest.mark.parametrize(
    ("claim", "claim_type", "reason"),
    [
        ("Целевая цена бумаги равна 100.", "valuation", "target_price_claim_forbidden"),
        ("Сформирован новый прогноз.", "financial_projection", "unsupported_forecast_claim"),
    ],
)
def test_ai_cannot_create_target_price_or_financial_projection(claim, claim_type, reason):
    finding = _finding(claim_type=claim_type)
    finding = finding.model_copy(update={"claim": claim})
    reduced = reduce_findings([AnalystOutput(analyst="market", findings=[finding])])
    result = preverify_findings(reduced, {"market_snapshot.json": {"asof": "2026-08-10"}})
    assert reason in result.rejected[finding.id]


def test_partial_pit_warning_is_mandatory_and_confidence_is_reduced():
    finding = _finding(
        "stock_fundamental",
        agent="stock",
        entity_type="stock",
        source_ref="stocks/SBER.json#fundamentals.revenue",
        value=100,
    )
    reduced = reduce_findings([AnalystOutput(analyst="stock", findings=[finding])])
    result = preverify_findings(
        reduced,
        {"stocks/SBER.json": {"fundamentals": {"revenue": 100}}},
    )
    assert result.findings[0].confidence == 0.6
    assert "point-in-time lineage is partial" in result.findings[0].warnings[0]


def test_verifier_cannot_drop_ids_and_forced_partial_survives():
    finding = _finding()
    with pytest.raises(ValueError, match="match input"):
        validate_verifier_output(VerifierOutput(decisions=[]), [finding], {})

    from src.research.ai.schemas import VerificationDecision

    output = VerifierOutput(
        decisions=[
            VerificationDecision(
                finding_id=finding.id,
                status="PASS",
                adjusted_confidence=0.9,
                reasons=["evidence_valid"],
            )
        ]
    )
    normalized = validate_verifier_output(output, [finding], {finding.id: ["pit_partial"]})
    passed, partial, rejected = split_verified([finding], normalized)
    assert passed == []
    assert len(partial) == 1
    assert partial[0].confidence == finding.confidence
    assert "pit_partial" in partial[0].warnings
    assert rejected == {}


class _InvalidJSONModels:
    def __init__(self, *, capacity: bool = False):
        self.calls = 0
        self.capacity = capacity
        self.last_kwargs = None

    async def generate_content(self, **_kwargs):
        self.calls += 1
        self.last_kwargs = _kwargs
        if self.capacity:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return SimpleNamespace(parsed=None, text="{", usage_metadata=None)


def _bare_gemini(config: AIConfig, models: _InvalidJSONModels) -> GeminiClient:
    client = GeminiClient.__new__(GeminiClient)
    client.config = config
    client.budget = RequestBudget(config.max_ai_requests_per_run)
    client._semaphore = asyncio.Semaphore(config.max_parallel_requests)
    client._types = SimpleNamespace(GenerateContentConfig=lambda **kwargs: kwargs)
    client._client = SimpleNamespace(aio=SimpleNamespace(models=models))
    client.rate_limit_errors_total = 0
    client.retry_count_total = 0
    return client


def test_malformed_structured_response_retry_is_bounded():
    config = AIConfig(max_retries=2)
    models = _InvalidJSONModels()
    client = _bare_gemini(config, models)
    with pytest.raises(TechnicalAIError):
        asyncio.run(
            client.generate(
                node="market",
                system_prompt="test",
                payload={"safe": True},
                response_model=AnalystOutput,
                model=config.analyst_model,
            )
        )
    assert models.calls == 3
    assert client.budget.requests == 3
    assert "response_json_schema" in models.last_kwargs["config"]
    assert "response_schema" not in models.last_kwargs["config"]


def test_gemini_schema_uses_supported_subset():
    schema = _gemini_json_schema(AnalystOutput)
    encoded = json.dumps(schema, sort_keys=True)

    assert '"minLength"' not in encoded
    assert '"maxLength"' not in encoded
    assert '"default"' not in encoded
    assert schema["type"] == "object"
    assert "findings" in schema["properties"]
    assert schema["properties"]["analyst"]["enum"] == [
        "market",
        "macro",
        "equity",
        "bonds",
        "banks",
        "news",
        "stock",
    ]


def test_429_is_bounded_and_never_switches_provider():
    config = AIConfig(max_retries=1)
    models = _InvalidJSONModels(capacity=True)
    client = _bare_gemini(config, models)
    with pytest.raises(CapacityError, match="free-tier capacity"):
        asyncio.run(
            client.generate(
                node="market",
                system_prompt="test",
                payload={"safe": True},
                response_model=AnalystOutput,
                model=config.analyst_model,
            )
        )
    assert models.calls == 2
    assert client.config.billing_allowed is False
