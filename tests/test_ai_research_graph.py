from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from src.research.ai.analysts import (
    AnalystTask,
    committee_tasks,
    run_analyst,
    run_parallel_analysts,
    stock_task,
)
from src.research.ai.artifacts import ResearchState, validate_ai_output_dir
from src.research.ai.client import (
    DeterministicMockAIClient,
    Generated,
    RequestBudget,
    TechnicalAIError,
)
from src.research.ai.config import AIConfig
from src.research.ai.eligibility import select_stock_universe
from src.research.ai.orchestrator import CriticalGraphError, ResearchGraph
from src.research.ai.wire import WireVerifierOutput
from tests.test_research_state import _build


def _fixture(tmp_path: Path, *, delay: float = 0, max_stocks: int = 0):
    _, research_dir, _ = _build(tmp_path)
    config = AIConfig(
        max_stock_memos_per_run=max_stocks,
        max_parallel_requests=6,
        output_dir=tmp_path / "ai",
    )
    client = DeterministicMockAIClient(RequestBudget(config.max_ai_requests_per_run), delay_seconds=delay)
    graph = ResearchGraph(
        config=config,
        client=client,
        research_dir=research_dir,
        output_dir=config.output_dir,
    )
    return graph, client, research_dir, config


class FaultClient(DeterministicMockAIClient):
    def __init__(self, budget: RequestBudget, failures: set[str]):
        super().__init__(budget)
        self.failures = failures
        self.payloads: dict[str, dict] = {}

    async def generate(self, **kwargs):
        node = kwargs["node"]
        self.payloads[node] = kwargs["payload"]
        if node in self.failures:
            await self.budget.reserve()
            self.calls.append(node)
            raise TechnicalAIError(f"injected failure: {node}")
        return await super().generate(**kwargs)


class RejectNewsClient(FaultClient):
    async def generate(self, **kwargs):
        generated = await super().generate(**kwargs)
        if kwargs["node"] != "verifier":
            return generated
        decisions = []
        for decision in generated.value.results:
            if decision.finding_id.startswith("news:"):
                decision = decision.model_copy(
                    update={"verdict": "REJECT", "reason": "unsupported_news_claim"}
                )
            decisions.append(decision)
        return Generated(
            value=WireVerifierOutput(results=decisions, warnings=generated.value.warnings),
            usage=generated.usage,
        )


def test_independent_analysts_run_in_parallel(tmp_path):
    graph, client, research_dir, config = _fixture(tmp_path, delay=0.08)
    state = ResearchState(research_dir)
    started = time.perf_counter()
    results = asyncio.run(
        run_parallel_analysts(
            committee_tasks(state.artifacts, state.manifest),
            client=client,
            config=config,
            manifest=state.manifest,
        )
    )
    elapsed = time.perf_counter() - started
    assert all(result.error is None for result in results)
    assert elapsed < 0.30
    assert len(client.calls) == 6


def test_noncritical_analyst_failure_degrades_graph(tmp_path):
    _, research_dir, _ = _build(tmp_path)
    config = AIConfig(max_stock_memos_per_run=0, output_dir=tmp_path / "ai")
    client = FaultClient(RequestBudget(config.max_ai_requests_per_run), {"news"})
    result = asyncio.run(
        ResearchGraph(
            config=config, client=client, research_dir=research_dir, output_dir=config.output_dir
        ).run()
    )
    assert result["market_memo"].status == "partial"
    assert "analyst_failed:news" in result["market_memo"].data_quality
    assert result["telemetry"].nodes["analyst:news"].critical is False
    assert result["status"]["publishable"] is True


@pytest.mark.parametrize("node", ["verifier", "synthesizer"])
def test_critical_ai_node_failure_blocks_publication(tmp_path, node):
    _, research_dir, _ = _build(tmp_path)
    output = tmp_path / "ai"
    output.mkdir()
    sentinel = output / "market_memo.json"
    sentinel.write_text('{"last_good":true}', encoding="utf-8")
    config = AIConfig(max_stock_memos_per_run=0, output_dir=output)
    client = FaultClient(RequestBudget(config.max_ai_requests_per_run), {node})
    with pytest.raises(TechnicalAIError):
        asyncio.run(
            ResearchGraph(config=config, client=client, research_dir=research_dir, output_dir=output).run()
        )
    assert sentinel.read_text(encoding="utf-8") == '{"last_good":true}'


def test_reducer_failure_is_critical(tmp_path, monkeypatch):
    graph, _, _, _ = _fixture(tmp_path)

    def fail(_outputs):
        raise RuntimeError("reducer broken")

    monkeypatch.setattr("src.research.ai.orchestrator.reduce_findings", fail)
    with pytest.raises(CriticalGraphError, match="reducer failed"):
        asyncio.run(graph.run())


def test_rejected_finding_never_reaches_synthesizer(tmp_path):
    _, research_dir, _ = _build(tmp_path)
    config = AIConfig(max_stock_memos_per_run=0, output_dir=tmp_path / "ai")
    client = RejectNewsClient(RequestBudget(config.max_ai_requests_per_run), set())
    result = asyncio.run(
        ResearchGraph(
            config=config, client=client, research_dir=research_dir, output_dir=config.output_dir
        ).run()
    )
    synthesis_ids = {row["id"] for row in client.payloads["synthesizer"]["findings"]}
    news_id = next(item for item in result["status"]["rejected_finding_ids"] if item.startswith("news:"))
    assert news_id not in synthesis_ids
    assert news_id not in result["market_memo"].key_findings
    assert client.calls.count("verifier") == 1


def test_unchanged_state_reuses_validated_cache(tmp_path):
    graph, client, _, config = _fixture(tmp_path, max_stocks=1)
    first = asyncio.run(graph.run())
    assert first["telemetry"].requests == 11
    cached_client = DeterministicMockAIClient(RequestBudget(config.max_ai_requests_per_run))
    second = asyncio.run(
        ResearchGraph(
            config=config,
            client=cached_client,
            research_dir=graph.research_dir,
            output_dir=config.output_dir,
        ).run()
    )
    assert second["telemetry"].requests == 0
    assert second["telemetry"].cache_hits == 2
    assert cached_client.calls == []
    assert validate_ai_output_dir(config.output_dir) == []


@pytest.mark.parametrize(
    "unsafe",
    [
        {"quantity": 10},
        {"source_file": "/" + "Users/example/private.json"},
        {"api_key": "AI" + "zaabcdefghijklmnopqrstuvwxyz123456"},
    ],
)
def test_unsafe_payload_is_rejected_before_ai_call(tmp_path, unsafe):
    graph, client, _, config = _fixture(tmp_path)
    task = AnalystTask("market", {"market_snapshot.json": unsafe}, critical=True)
    result = asyncio.run(
        run_analyst(task, client=client, config=config, manifest={"research_asof": "2026-08-11"})
    )
    assert isinstance(result.error, ValueError)
    assert client.calls == []


def test_production_validator_rejects_mock_artifact(tmp_path):
    graph, _, _, config = _fixture(tmp_path)
    asyncio.run(graph.run())
    errors = validate_ai_output_dir(config.output_dir, require_real=True)
    assert "mock AI artifact cannot be published as production research" in errors


def test_stock_evidence_registry_contains_selected_snapshot(tmp_path):
    graph, client, _, _ = _fixture(tmp_path, max_stocks=1)
    result = asyncio.run(graph.run())
    ticker = result["status"]["stock_memos"][0]
    assert f"stock:{ticker}" in client.calls
    assert result["stock_memos"][ticker].publishable is True
    assert result["stock_memos"][ticker].evidence


def test_priority_universe_uses_first_pass_sector_diversification(tmp_path):
    _, research_dir, _ = _build(tmp_path)
    state = ResearchState(research_dir)
    config = AIConfig(max_stock_memos_per_run=2)
    selection, stocks = select_stock_universe(research_dir, state.stock_index, config)
    sectors = {(stock.get("sector_context") or {}).get("sector") for stock in stocks.values()}
    assert len(selection.selected) == 2
    assert len(sectors) == 2


def test_bank_stock_receives_only_its_bank_specific_context():
    artifacts = {
        "market_snapshot.json": {"asof": "2026-08-10"},
        "sector_snapshot.json": {"asof": "2026-08-10"},
        "news_snapshot.json": {"asof": "2026-08-10"},
        "bank_snapshot.json": {
            "schema_version": 1,
            "asof": "2026-07-01",
            "data_quality": {"point_in_time_quality": "partial"},
            "banks": [
                {"ticker": "SBER", "roe_pct": 22.7, "cost_of_equity": 0.23},
                {"ticker": "VTBR", "roe_pct": 19.5, "cost_of_equity": 0.24},
            ],
        },
    }
    task = stock_task("SBER", {"ticker": "SBER", "asof": "2026-08-10"}, artifacts)
    bank = task.artifacts["stock_context/SBER_bank.json"]
    assert bank["bank"]["ticker"] == "SBER"
    assert "VTBR" not in str(bank)
