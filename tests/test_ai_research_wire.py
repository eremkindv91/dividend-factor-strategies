from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from src.research.ai.analysts import AnalystTask, run_analyst
from src.research.ai.client import DeterministicMockAIClient, RequestBudget, _gemini_json_schema
from src.research.ai.config import AIConfig
from src.research.ai.schemas import AnalystOutput, MarketMemo, StockMemo, VerifierOutput
from src.research.ai.wire import (
    WireAnalystOutput,
    WireFinding,
    WireMarketMemo,
    WireStockMemo,
    WireVerifierOutput,
    build_evidence_catalog,
    hydrate_analyst_output,
    schema_statistics,
    validate_wire_schema_compatibility,
)


def _wire_finding(**updates):
    payload = {
        "id": "market_state",
        "claim": "Рыночный срез имеет подтверждённую дату.",
        "entity_type": "market",
        "entity_id": "IMOEX",
        "claim_type": "state",
        "kind": "fact",
        "materiality": "medium",
        "confidence": 0.8,
        "causal": False,
        "evidence_refs": ["E0001"],
        "counter_evidence_refs": [],
        "warnings": [],
        "invalidation": ["Новый рыночный срез."],
    }
    payload.update(updates)
    return WireFinding.model_validate(payload)


def test_wire_schema_is_simpler_than_domain_schema():
    pairs = (
        (AnalystOutput, WireAnalystOutput),
        (VerifierOutput, WireVerifierOutput),
        (MarketMemo, WireMarketMemo),
        (StockMemo, WireStockMemo),
    )
    for domain, wire in pairs:
        domain_stats = schema_statistics(domain)
        wire_stats = schema_statistics(wire, schema=_gemini_json_schema(wire))
        assert wire_stats["bytes"] < domain_stats["bytes"]
        assert "$defs" not in wire_stats["constructs"]
        assert "$ref" not in wire_stats["constructs"]


def test_all_wire_schemas_pass_local_compatibility_check():
    diagnostics = [
        schema_statistics(model, schema=_gemini_json_schema(model))
        for model in (WireAnalystOutput, WireVerifierOutput, WireMarketMemo, WireStockMemo)
    ]
    assert validate_wire_schema_compatibility(diagnostics) == []


def test_wire_schema_generation_is_stable():
    first = json.dumps(_gemini_json_schema(WireAnalystOutput), sort_keys=True)
    second = json.dumps(_gemini_json_schema(WireAnalystOutput), sort_keys=True)
    assert first == second


def test_wire_evidence_uses_references_and_cannot_include_value():
    finding = _wire_finding()
    assert finding.evidence_refs == ["E0001"]
    assert "value" not in finding.model_dump()
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WireFinding.model_validate({**finding.model_dump(), "value": 123})


def test_evidence_reference_hydrates_from_source_state():
    catalog = build_evidence_catalog({"market_snapshot.json": {"asof": "2026-08-10"}})
    output = hydrate_analyst_output(
        WireAnalystOutput(findings=[_wire_finding()], warnings=[]),
        agent="market",
        catalog=catalog,
    )
    evidence = output.findings[0].evidence[0]
    assert evidence.metric == "asof"
    assert evidence.value == "2026-08-10"
    assert evidence.asof == "2026-08-10"
    assert evidence.source_ref == "market_snapshot.json#asof"


def test_unknown_evidence_ref_is_rejected():
    catalog = build_evidence_catalog({"market_snapshot.json": {"asof": "2026-08-10"}})
    wire = WireAnalystOutput(
        findings=[_wire_finding(evidence_refs=["E9999"])],
        warnings=[],
    )
    with pytest.raises(ValueError, match="unknown evidence refs"):
        hydrate_analyst_output(wire, agent="market", catalog=catalog)


def test_hydrated_finding_passes_full_domain_validation():
    catalog = build_evidence_catalog({"market_snapshot.json": {"asof": "2026-08-10"}})
    output = hydrate_analyst_output(
        WireAnalystOutput(findings=[_wire_finding()], warnings=[]),
        agent="market",
        catalog=catalog,
    )
    assert isinstance(output, AnalystOutput)
    assert output.findings[0].agent == "market"


class CapturingMock(DeterministicMockAIClient):
    def __init__(self, budget):
        super().__init__(budget)
        self.kwargs = None

    async def generate(self, **kwargs):
        self.kwargs = kwargs
        return await super().generate(**kwargs)


def test_complex_domain_schema_and_private_data_are_not_sent_to_gemini():
    config = AIConfig()
    client = CapturingMock(RequestBudget(config.max_ai_requests_per_run))
    task = AnalystTask(
        "market",
        {"market_snapshot.json": {"asof": "2026-08-10", "returns": {"IMOEX_20d": -0.04}}},
        critical=True,
    )
    result = asyncio.run(
        run_analyst(task, client=client, config=config, manifest={"research_asof": "2026-08-10"})
    )
    assert result.error is None
    assert client.kwargs["response_model"] is WireAnalystOutput
    assert "artifacts" not in client.kwargs["payload"]
    assert "evidence_catalog" in client.kwargs["payload"]
    encoded = json.dumps(client.kwargs["payload"])
    assert "quantity" not in encoded
    assert "purchase_price" not in encoded
    assert "localStorage" not in encoded
