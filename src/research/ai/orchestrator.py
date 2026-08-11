from __future__ import annotations

import asyncio
import json
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analysts import committee_tasks, run_analyst, run_parallel_analysts, stock_task
from .artifacts import ResearchState, validate_ai_output_dir, write_json_atomic
from .cache import ValidatedCache, ai_run_fingerprint, stock_ai_fingerprint
from .client import AIClient, RequestBudget, _safe_error_detail
from .config import AIConfig
from .eligibility import select_stock_universe
from .prompts import PROMPT_VERSIONS, system_prompt
from .reducer import reduce_findings
from .schemas import (
    AnalystOutput,
    Finding,
    MarketMemo,
    NodeStatus,
    RunTelemetry,
    StockMemo,
    VerificationDecision,
    VerifierOutput,
)
from .verification import preverify_findings, split_verified, validate_verifier_output
from .wire import (
    WireMarketMemo,
    WireStockMemo,
    WireVerifierOutput,
    hydrate_market_memo,
    hydrate_stock_memo,
    hydrate_verifier_output,
)


class CriticalGraphError(RuntimeError):
    pass


NUMBER_TOKEN = re.compile(r"(?<![A-Za-zА-Яа-я_])[+-]?\d+(?:[.,]\d+)?%?")
FORBIDDEN_MEMO_TEXT = re.compile(
    r"\b(?:целевая\s+цена|таргет\s+цены?|target\s+price|мы\s+прогнозируем|я\s+рассчитал)\b",
    re.IGNORECASE,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _finding_ids_in_market(memo: MarketMemo) -> set[str]:
    ids = set(memo.key_findings) | set(memo.regime.finding_ids)
    ids.update(finding.id for finding in memo.evidence)
    for field in (
        "sector_context", "equity_context", "bond_context", "bank_context", "catalysts",
        "contradictions", "risks", "watch",
    ):
        ids.update(getattr(memo, field).finding_ids)
    return ids


def _finding_ids_in_stock(memo: StockMemo) -> set[str]:
    ids = {finding.id for finding in memo.evidence}
    for field in (
        "investment_view", "market_context", "sector_context", "company_vs_sector", "valuation",
        "quality", "dividends", "momentum_positioning", "catalysts", "risks", "contradictions",
    ):
        ids.update(getattr(memo, field).finding_ids)
    return ids


def _memo_texts(memo: MarketMemo | StockMemo) -> list[str]:
    if isinstance(memo, MarketMemo):
        fields = [memo.summary, memo.regime.label]
        fields.extend(
            getattr(memo, key).summary
            for key in (
                "sector_context", "equity_context", "bond_context", "bank_context", "catalysts",
                "contradictions", "risks", "watch",
            )
        )
        return fields
    fields = list(memo.what_would_change_the_view)
    fields.extend(
        getattr(memo, key).summary
        for key in (
            "investment_view", "market_context", "sector_context", "company_vs_sector", "valuation",
            "quality", "dividends", "momentum_positioning", "catalysts", "risks", "contradictions",
        )
    )
    return fields


def _validate_memo(
    memo: MarketMemo | StockMemo,
    *,
    allowed_findings: list[Finding],
    rejected_ids: set[str],
) -> None:
    allowed_ids = {finding.id for finding in allowed_findings}
    overlap = allowed_ids & rejected_ids
    if overlap:
        raise CriticalGraphError(f"accepted/rejected finding IDs overlap: {sorted(overlap)}")
    used_ids = _finding_ids_in_market(memo) if isinstance(memo, MarketMemo) else _finding_ids_in_stock(memo)
    if not used_ids.issubset(allowed_ids):
        raise CriticalGraphError(f"memo references unknown findings: {sorted(used_ids - allowed_ids)}")
    if rejected_ids & used_ids:
        raise CriticalGraphError("rejected finding leaked into synthesis")
    allowed_numbers = {
        token.replace(",", ".")
        for finding in allowed_findings
        for value in (
            [finding.claim]
            + [str(evidence.value) for evidence in finding.evidence + finding.counter_evidence]
        )
        for token in NUMBER_TOKEN.findall(value)
    }
    unsupported = {
        token
        for text in _memo_texts(memo)
        for token in NUMBER_TOKEN.findall(text)
        if token.replace(",", ".") not in allowed_numbers
    }
    if unsupported:
        raise CriticalGraphError(f"synthesizer introduced unsupported numbers: {sorted(unsupported)}")
    if any(FORBIDDEN_MEMO_TEXT.search(text) for text in _memo_texts(memo)):
        raise CriticalGraphError("synthesizer introduced forbidden target/forecast language")


def _validate_stock_context_chain(
    memo: StockMemo,
    *,
    ticker: str,
    findings: list[Finding],
    bank_context_required: bool,
) -> None:
    refs = {
        evidence.source_ref
        for finding in findings
        for evidence in finding.evidence + finding.counter_evidence
    }
    required_prefixes = {
        "market": "market_snapshot.json#",
        "sector": "sector_snapshot.json#",
        "company": f"stocks/{ticker}.json#",
    }
    if bank_context_required:
        required_prefixes["bank"] = f"stock_context/{ticker}_bank.json#"
    missing = [name for name, prefix in required_prefixes.items() if not any(ref.startswith(prefix) for ref in refs)]
    if missing:
        raise CriticalGraphError(f"stock memo lacks required context evidence: {', '.join(missing)}")
    for name in ("market_context", "sector_context", "company_vs_sector"):
        section = getattr(memo, name)
        if not section.summary.strip() or not section.finding_ids:
            raise CriticalGraphError(f"stock memo has empty required section: {name}")


def _verification_payload(
    findings: list[Finding],
    conflicts: dict[str, list[str]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    return {
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "conflicts": conflicts,
        "temporal_warnings": manifest.get("temporal_warnings") or [],
        "component_eligibility": manifest.get("component_eligibility") or {},
        "survivorship_status": manifest.get("survivorship_status"),
    }


class ResearchGraph:
    def __init__(
        self,
        *,
        config: AIConfig,
        client: AIClient,
        research_dir: Path,
        output_dir: Path | None = None,
    ):
        self.config = config
        self.client = client
        self.research_dir = research_dir
        self.output_dir = output_dir or config.output_dir
        self.cache = ValidatedCache(self.output_dir)
        self.usages = []

    def _node(self, telemetry: RunTelemetry, name: str, **kwargs: Any) -> None:
        telemetry.nodes[name] = NodeStatus(**kwargs)

    def _usage(self, telemetry: RunTelemetry, usage) -> None:
        self.usages.append(usage)
        telemetry.requests += 1
        telemetry.input_tokens += usage.input_tokens or 0
        telemetry.output_tokens += usage.output_tokens or 0
        telemetry.rate_limit_errors += usage.rate_limit_errors
        telemetry.retries += max(0, usage.attempts - 1)

    async def _verify(
        self,
        *,
        node: str,
        findings: list[Finding],
        conflicts: dict[str, list[str]],
        forced_partial: dict[str, list[str]],
        manifest: dict[str, Any],
        telemetry: RunTelemetry,
    ) -> VerifierOutput:
        started = time.perf_counter()
        generated = await self.client.generate(
            node=node,
            system_prompt=system_prompt("verifier"),
            payload=_verification_payload(findings, conflicts, manifest),
            response_model=WireVerifierOutput,
            model=self.config.verifier_model,
        )
        self._usage(telemetry, generated.usage)
        verified = validate_verifier_output(hydrate_verifier_output(generated.value), findings, forced_partial)
        self._node(
            telemetry, node, status="success", critical=True,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return verified

    async def _synthesize_market(
        self,
        *,
        findings: list[Finding],
        conflicts: dict[str, list[str]],
        warnings: list[str],
        summary: dict[str, int],
        manifest: dict[str, Any],
        telemetry: RunTelemetry,
    ) -> MarketMemo:
        started = time.perf_counter()
        generated_at = _now_iso()
        generated = await self.client.generate(
            node="synthesizer",
            system_prompt=system_prompt("synthesizer"),
            payload={
                "memo_type": "market",
                "findings": [finding.model_dump(mode="json") for finding in findings],
                "conflicts": conflicts,
                "warnings": warnings,
                "verification_summary": summary,
                "metadata": {
                    "research_asof": manifest.get("research_asof"),
                    "generated_at": generated_at,
                },
            },
            response_model=WireMarketMemo,
            model=self.config.synthesizer_model,
        )
        self._usage(telemetry, generated.usage)
        self._node(
            telemetry, "synthesizer", status="success", critical=True,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return hydrate_market_memo(
            generated.value,
            asof=str(manifest.get("research_asof") or ""),
            generated_at=generated_at,
            status="partial" if warnings else "complete",
        )

    async def _synthesize_stock(
        self,
        *,
        ticker: str,
        findings: list[Finding],
        warnings: list[str],
        manifest: dict[str, Any],
        telemetry: RunTelemetry,
    ) -> StockMemo:
        node = f"stock_synthesizer:{ticker}"
        generated_at = _now_iso()
        generated = await self.client.generate(
            node=node,
            system_prompt=system_prompt("synthesizer"),
            payload={
                "memo_type": "stock",
                "findings": [finding.model_dump(mode="json") for finding in findings],
                "warnings": warnings,
                "metadata": {
                    "ticker": ticker,
                    "research_asof": manifest.get("research_asof"),
                    "generated_at": generated_at,
                },
            },
            response_model=WireStockMemo,
            model=self.config.synthesizer_model,
        )
        self._usage(telemetry, generated.usage)
        self._node(telemetry, node, status="success", critical=True, duration_ms=generated.usage.duration_ms)
        return hydrate_stock_memo(
            generated.value,
            ticker=ticker,
            asof=str(manifest.get("research_asof") or ""),
            generated_at=generated_at,
            status="partial" if warnings else "complete",
        )

    async def _run_stock(
        self,
        *,
        ticker: str,
        stock: dict[str, Any],
        state: ResearchState,
        manifest: dict[str, Any],
        stock_fingerprint: str,
        telemetry: RunTelemetry,
    ) -> tuple[StockMemo | None, set[str]]:
        task = stock_task(ticker, stock, state.artifacts)
        state.artifacts.update(task.artifacts)
        cached = self.cache.stock(ticker, stock_fingerprint)
        if cached:
            telemetry.cache_hits += 1
            self._node(telemetry, f"stock:{ticker}", status="cached", critical=False)
            return StockMemo.model_validate(cached), set()
        result = await run_analyst(
            task,
            client=self.client,
            config=self.config,
            manifest=manifest,
        )
        if result.error or not result.output or not result.output.findings:
            self._node(
                telemetry, f"stock:{ticker}", status="failed", critical=False,
                error_type=type(result.error).__name__ if result.error else "NoFindings",
                duration_ms=result.duration_ms,
            )
            return None, set()
        if result.usage:
            self._usage(telemetry, result.usage)
        self._node(telemetry, f"stock:{ticker}", status="success", critical=False, duration_ms=result.duration_ms)
        reduced = reduce_findings([result.output])
        pre = preverify_findings(reduced, state.artifacts)
        if not pre.findings:
            return None, set(pre.rejected)
        verifier_node = f"stock_verifier:{ticker}"
        verifier = await self._verify(
            node=verifier_node,
            findings=pre.findings,
            conflicts=reduced.conflicts,
            forced_partial=pre.forced_partial,
            manifest=manifest,
            telemetry=telemetry,
        )
        passed, partial, rejected = split_verified(pre.findings, verifier)
        rejected_ids = set(pre.rejected) | set(rejected)
        usable = passed + partial
        if not usable:
            return None, rejected_ids
        warnings = sorted(
            set(manifest.get("temporal_warnings") or [])
            | {warning for finding in partial for warning in finding.warnings}
        )
        memo = await self._synthesize_stock(
            ticker=ticker,
            findings=usable,
            warnings=warnings,
            manifest=manifest,
            telemetry=telemetry,
        )
        memo = memo.model_copy(
            update={
                "status": "partial" if partial or warnings else "complete",
                "data_quality": sorted(set(memo.data_quality + warnings)),
                "evidence": usable,
                "stock_ai_fingerprint": stock_fingerprint,
                "publishable": True,
            }
        )
        _validate_memo(memo, allowed_findings=usable, rejected_ids=rejected_ids)
        _validate_stock_context_chain(
            memo,
            ticker=ticker,
            findings=usable,
            bank_context_required=f"stock_context/{ticker}_bank.json" in task.artifacts,
        )
        return memo, rejected_ids

    async def run(self) -> dict[str, Any]:
        started_perf = time.perf_counter()
        started_at = _now_iso()
        state = ResearchState(self.research_dir)
        manifest = state.manifest
        if manifest.get("ai_input_ready") is not True:
            raise CriticalGraphError("research manifest is not ai_input_ready")
        if manifest.get("cross_domain_ready") is not True:
            raise CriticalGraphError("research manifest is not cross_domain_ready")
        run_fingerprint = ai_run_fingerprint(manifest, self.config)
        run_id = f"air-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        telemetry = RunTelemetry(
            run_id=run_id,
            ai_run_fingerprint=run_fingerprint,
            graph_version=self.config.graph_version,
            prompt_versions=PROMPT_VERSIONS,
            model_settings=self.config.public_model_config(),
            started_at=started_at,
            billing_allowed=False,
            estimated_cost=0 if self.config.execution_mode == "mock" or self.config.free_tier_verified else None,
        )

        selection, selected_stocks = select_stock_universe(
            self.research_dir,
            state.stock_index,
            self.config,
            previous_fingerprints=self.cache.previous_stock_fingerprints(),
        )
        stock_fingerprints = {
            ticker: stock_ai_fingerprint(
                stock_fingerprint=state.stock_fingerprint(ticker), manifest=manifest, config=self.config
            )
            for ticker in selection.selected
        }
        market_cached = self.cache.market(run_fingerprint)
        stock_memos: dict[str, StockMemo] = {}
        rejected_ids: set[str] = set()

        if market_cached:
            market_memo = MarketMemo.model_validate(market_cached)
            telemetry.cache_hits += 1
            self._node(telemetry, "market_graph", status="cached", critical=True)
            verifier_executed = True
        else:
            results = await run_parallel_analysts(
                committee_tasks(state.artifacts, manifest),
                client=self.client,
                config=self.config,
                manifest=manifest,
            )
            outputs: list[AnalystOutput] = []
            failed_domains: list[str] = []
            for result in results:
                node_name = f"analyst:{result.name}"
                if result.error or result.output is None:
                    failed_domains.append(result.name)
                    self._node(
                        telemetry, node_name, status="failed", critical=result.critical,
                        error_type=type(result.error).__name__ if result.error else "UnknownError",
                        duration_ms=result.duration_ms,
                    )
                    if result.critical:
                        detail = f"{type(result.error).__name__}: {result.error}"[:500]
                        raise CriticalGraphError(
                            f"required analyst failed: {result.name} ({detail})"
                        ) from result.error
                    continue
                if result.usage:
                    self._usage(telemetry, result.usage)
                self._node(
                    telemetry, node_name, status="success", critical=result.critical,
                    duration_ms=result.duration_ms,
                )
                outputs.append(result.output)
            market_output = next((output for output in outputs if output.analyst == "market"), None)
            if market_output is None or not market_output.findings:
                raise CriticalGraphError("market analyst produced no usable findings")

            reducer_started = time.perf_counter()
            try:
                reduced = reduce_findings(outputs)
            except Exception as exc:  # noqa: BLE001 - critical boundary
                self._node(telemetry, "reducer", status="failed", critical=True, error_type=type(exc).__name__)
                raise CriticalGraphError("deterministic reducer failed") from exc
            self._node(
                telemetry, "reducer", status="success", critical=True,
                duration_ms=round((time.perf_counter() - reducer_started) * 1000),
            )
            telemetry.raw_findings = sum(len(output.findings) for output in outputs)
            telemetry.reduced_findings = len(reduced.findings)
            telemetry.compression_ratio = (
                round(1 - telemetry.reduced_findings / telemetry.raw_findings, 6)
                if telemetry.raw_findings else None
            )
            pre = preverify_findings(reduced, state.artifacts)
            rejected_ids.update(pre.rejected)
            if not any(finding.agent == "market" for finding in pre.findings):
                raise CriticalGraphError("all market findings failed programmatic verification")
            verifier = await self._verify(
                node="verifier",
                findings=pre.findings,
                conflicts=reduced.conflicts,
                forced_partial=pre.forced_partial,
                manifest=manifest,
                telemetry=telemetry,
            )
            verifier_executed = True
            passed, partial, rejected = split_verified(pre.findings, verifier)
            rejected_ids.update(rejected)
            telemetry.verified_pass = len(passed)
            telemetry.verified_partial = len(partial)
            telemetry.verified_reject = len(rejected_ids)
            usable = passed + partial
            if not any(finding.agent == "market" for finding in usable):
                raise CriticalGraphError("verifier rejected all required market findings")
            accepted_ids = {finding.id for finding in usable}
            if accepted_ids & rejected_ids:
                raise CriticalGraphError(
                    f"accepted/rejected finding IDs overlap: {sorted(accepted_ids & rejected_ids)}"
                )
            warnings = sorted(
                set(manifest.get("temporal_warnings") or [])
                | {f"analyst_failed:{name}" for name in failed_domains}
                | {warning for finding in partial for warning in finding.warnings}
            )
            summary = {"pass": len(passed), "partial": len(partial), "reject": len(rejected_ids)}
            market_memo = await self._synthesize_market(
                findings=usable,
                conflicts=reduced.conflicts,
                warnings=warnings,
                summary=summary,
                manifest=manifest,
                telemetry=telemetry,
            )
            market_memo = market_memo.model_copy(
                update={
                    "status": "partial" if partial or failed_domains or warnings else "complete",
                    "data_quality": sorted(set(market_memo.data_quality + warnings)),
                    "verification_summary": summary,
                    "evidence": usable,
                    "ai_run_fingerprint": run_fingerprint,
                    "publishable": True,
                }
            )
            _validate_memo(market_memo, allowed_findings=usable, rejected_ids=rejected_ids)

        for ticker, stock in selected_stocks.items():
            try:
                memo, stock_rejected = await self._run_stock(
                    ticker=ticker,
                    stock=stock,
                    state=state,
                    manifest=manifest,
                    stock_fingerprint=stock_fingerprints[ticker],
                    telemetry=telemetry,
                )
                rejected_ids.update(stock_rejected)
                if memo:
                    stock_memos[ticker] = memo
            except Exception as exc:  # noqa: BLE001 - stock failure cannot break market memo
                detail = _safe_error_detail(exc)
                telemetry.warnings.append(
                    f"stock_memo_failed:{ticker}:{type(exc).__name__}:{detail}"
                )
                self._node(
                    telemetry, f"stock_graph:{ticker}", status="failed", critical=False,
                    error_type=type(exc).__name__, warning=detail,
                )

        telemetry.finished_at = _now_iso()
        telemetry.duration_ms = round((time.perf_counter() - started_perf) * 1000)
        budget = getattr(self.client, "budget", None)
        telemetry.requests = max(telemetry.requests, int(getattr(budget, "requests", 0)))
        telemetry.rate_limit_errors = max(
            telemetry.rate_limit_errors,
            int(getattr(self.client, "rate_limit_errors_total", 0)),
        )
        telemetry.retries = max(
            telemetry.retries,
            int(getattr(self.client, "retry_count_total", 0)),
        )
        telemetry.publishable = True
        status = {
            "schema_version": 1,
            "generated_at": telemetry.finished_at,
            "run_id": run_id,
            "research_input_hash": manifest.get("research_input_hash"),
            "ai_run_fingerprint": run_fingerprint,
            "publishable": True,
            "verifier_executed": verifier_executed,
            "market_memo_status": market_memo.status,
            "stock_memos": sorted(stock_memos),
            "stock_state_fingerprints": {
                row["ticker"]: row["fingerprint"] for row in state.stock_index.get("stocks", [])
            },
            "universe": selection.model_dump(mode="json"),
            "temporal_warnings": manifest.get("temporal_warnings") or [],
            "rejected_finding_ids": sorted(rejected_ids),
            "billing_allowed": False,
            "free_tier_verified": self.config.free_tier_verified,
            "execution_mode": self.config.execution_mode,
        }

        with tempfile.TemporaryDirectory(prefix="research-ai-stage-") as temp:
            staging = Path(temp)
            write_json_atomic(staging / "market_memo.json", market_memo.model_dump(mode="json"))
            write_json_atomic(
                staging / "run_metadata.json",
                telemetry.model_dump(mode="json", by_alias=True),
            )
            write_json_atomic(staging / "status.json", status)
            for ticker, memo in stock_memos.items():
                write_json_atomic(staging / "stocks" / f"{ticker}.json", memo.model_dump(mode="json"))
            errors = validate_ai_output_dir(staging)
            if errors:
                raise CriticalGraphError("AI publication validation failed: " + "; ".join(errors))
            write_json_atomic(self.output_dir / "market_memo.json", market_memo.model_dump(mode="json"))
            write_json_atomic(
                self.output_dir / "run_metadata.json",
                telemetry.model_dump(mode="json", by_alias=True),
            )
            write_json_atomic(self.output_dir / "status.json", status)
            for ticker, memo in stock_memos.items():
                write_json_atomic(self.output_dir / "stocks" / f"{ticker}.json", memo.model_dump(mode="json"))

        return {
            "market_memo": market_memo,
            "stock_memos": stock_memos,
            "status": status,
            "telemetry": telemetry,
        }


def run_graph(graph: ResearchGraph) -> dict[str, Any]:
    return asyncio.run(graph.run())
