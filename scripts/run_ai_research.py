#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.ai.client import (  # noqa: E402
    DeterministicMockAIClient,
    GeminiClient,
    RequestBudget,
    _gemini_json_schema,
    _safe_error_detail,
)
from src.research.ai.config import AIConfig  # noqa: E402
from src.research.ai.orchestrator import CriticalGraphError, ResearchGraph  # noqa: E402
from src.research.ai.schemas import AnalystOutput, MarketMemo, StockMemo, VerifierOutput  # noqa: E402
from src.research.ai.wire import (  # noqa: E402
    MARKET_SECTIONS,
    STOCK_SECTIONS,
    WireAnalystOutput,
    WireMarketMemo,
    WireStockMemo,
    WireVerifierOutput,
    schema_statistics,
    validate_wire_schema_compatibility,
)


ANALYST_FALLBACKS = (
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
)
VERIFIER_FALLBACKS = (
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)


class StructuredProbe(BaseModel):
    status: Literal["ok"]
    provider: Literal["gemini"]


def _available_model(configured: str, available: set[str], fallbacks: tuple[str, ...]) -> tuple[str, bool]:
    if configured in available:
        return configured, False
    replacement = next((model for model in fallbacks if model in available), None)
    if replacement is None:
        raise RuntimeError(
            f"configured Gemini model {configured} is unavailable and no allowlisted Flash fallback exists"
        )
    return replacement, True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the validated Gemini research graph")
    parser.add_argument("--mode", choices=("mock", "real"), default=None)
    parser.add_argument("--research-dir", type=Path, default=ROOT / "site" / "data" / "research")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "site" / "data" / "research" / "ai")
    parser.add_argument("--tickers", default="", help="Comma-separated explicit stock memo universe")
    parser.add_argument("--max-stock-memos", type=int, default=None)
    parser.add_argument("--list-models", action="store_true", help="List models visible to the configured Gemini key")
    parser.add_argument("--probe-structured-output", action="store_true")
    parser.add_argument("--probe-wire-schemas", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    return parser.parse_args(argv)


def _wire_probe_cases(config: AIConfig):
    analyst_example = {"findings": [], "warnings": []}
    market_sections = [
        {"key": key, "summary": "Нет проверенных выводов.", "finding_ids": []}
        for key in sorted(MARKET_SECTIONS)
    ]
    stock_sections = [
        {"key": key, "summary": "Нет проверенных выводов.", "finding_ids": []}
        for key in sorted(STOCK_SECTIONS)
    ]
    cases = [
        (role, config.analyst_model, WireAnalystOutput, analyst_example)
        for role in ("market", "macro", "equity", "bonds", "banks", "news")
    ]
    cases.extend(
        [
            (
                "verifier",
                config.verifier_model,
                WireVerifierOutput,
                {"results": [], "warnings": []},
            ),
            (
                "market_synthesizer",
                config.synthesizer_model,
                WireMarketMemo,
                {
                    "regime_label": "нет данных",
                    "regime_confidence": "low",
                    "summary": "Нет проверенных выводов.",
                    "key_finding_ids": [],
                    "sections": market_sections,
                },
            ),
            (
                "stock_synthesizer",
                config.synthesizer_model,
                WireStockMemo,
                {"confidence": "low", "sections": stock_sections, "invalidation": []},
            ),
        ]
    )
    return cases


async def _probe_wire_schemas(client: GeminiClient, config: AIConfig) -> tuple[list[dict], list[dict]]:
    wire_models = (WireAnalystOutput, WireVerifierOutput, WireMarketMemo, WireStockMemo)
    diagnostics = [
        schema_statistics(model, schema=_gemini_json_schema(model))
        for model in wire_models
    ]
    errors = validate_wire_schema_compatibility(diagnostics)
    domain = [schema_statistics(model) for model in (AnalystOutput, VerifierOutput, MarketMemo, StockMemo)]
    print(
        json.dumps(
            {
                "wire_schema_diagnostics": diagnostics,
                "domain_schema_diagnostics": domain,
                "local_compatibility_errors": errors,
            },
            ensure_ascii=False,
        )
    )
    if errors:
        raise RuntimeError("wire schema compatibility validation failed: " + "; ".join(errors))
    matrix: list[dict] = []
    for name, model, response_model, example in _wire_probe_cases(config):
        try:
            generated = await client.generate(
                node=f"wire_schema_probe:{name}",
                system_prompt="Return the example object exactly and do not add commentary.",
                payload={"example": example},
                response_model=response_model,
                model=model,
            )
            row = {
                "probe": name,
                "model": model,
                "schema": response_model.__name__,
                "http_status": 200,
                "structured_parse_status": "pass",
                "input_tokens": generated.usage.input_tokens,
                "output_tokens": generated.usage.output_tokens,
            }
        except Exception as exc:  # noqa: BLE001 - probe reports provider compatibility
            detail = _safe_error_detail(exc)
            row = {
                "probe": name,
                "model": model,
                "schema": response_model.__name__,
                "http_status": 400 if "400" in detail else None,
                "structured_parse_status": "fail",
                "error_type": type(exc).__name__,
                "error": detail,
            }
            print(json.dumps({"wire_schema_probe_result": row}, ensure_ascii=False))
            raise RuntimeError(f"wire schema probe failed: {name}") from exc
        matrix.append(row)
        print(json.dumps({"wire_schema_probe_result": row}, ensure_ascii=False))
    return diagnostics, matrix


async def _run(args: argparse.Namespace) -> dict:
    config = AIConfig.from_env()
    updates: dict = {"output_dir": args.output_dir}
    if args.mode:
        updates["execution_mode"] = args.mode
    if args.max_stock_memos is not None:
        updates["max_stock_memos_per_run"] = args.max_stock_memos
    tickers = [item.strip().upper() for item in args.tickers.split(",") if item.strip()]
    if tickers:
        updates.update({"stock_universe_mode": "explicit", "explicit_tickers": tickers})
    config = config.model_copy(update=updates)
    config = AIConfig.model_validate(config.model_dump())

    budget = RequestBudget(config.max_ai_requests_per_run)
    selected_models = {
        "analyst": config.analyst_model,
        "verifier": config.verifier_model,
        "synthesizer": config.synthesizer_model,
    }
    model_fallbacks: dict[str, bool] = {key: False for key in selected_models}
    available: list[str] = []
    wire_schema_diagnostics: list[dict] = []
    wire_schema_matrix: list[dict] = []
    if config.execution_mode == "real":
        client = GeminiClient(config, budget)
        available = await client.list_models()
        available_set = set(available)
        analyst, model_fallbacks["analyst"] = _available_model(
            config.analyst_model, available_set, ANALYST_FALLBACKS
        )
        verifier, model_fallbacks["verifier"] = _available_model(
            config.verifier_model, available_set, VERIFIER_FALLBACKS
        )
        synthesizer, model_fallbacks["synthesizer"] = _available_model(
            config.synthesizer_model, available_set, VERIFIER_FALLBACKS
        )
        config = AIConfig.model_validate(
            config.model_copy(
                update={
                    "analyst_model": analyst,
                    "verifier_model": verifier,
                    "synthesizer_model": synthesizer,
                }
            ).model_dump()
        )
        client.config = config
        selected_models = {"analyst": analyst, "verifier": verifier, "synthesizer": synthesizer}
        if args.list_models:
            print(json.dumps({"available_models": available}, ensure_ascii=False))
        if args.probe_structured_output:
            probe = await client.generate(
                node="structured_output_probe",
                system_prompt="Return the requested schema exactly. Do not add commentary.",
                payload={"status": "ok", "provider": "gemini"},
                response_model=StructuredProbe,
                model=config.analyst_model,
            )
            if probe.value.status != "ok":
                raise RuntimeError("Gemini structured-output probe returned an invalid status")
        if args.probe_wire_schemas:
            wire_schema_diagnostics, wire_schema_matrix = await _probe_wire_schemas(client, config)
    else:
        client = DeterministicMockAIClient(budget)
        if args.list_models:
            print(json.dumps({"available_models": await client.list_models()}, ensure_ascii=False))

    if args.preflight_only:
        if config.execution_mode != "real":
            raise ValueError("--preflight-only requires --mode real")
        return {
            "ok": True,
            "execution_mode": "real",
            "billing_allowed": False,
            "estimated_cost": 0 if config.free_tier_verified else None,
            "free_tier_verified": config.free_tier_verified,
            "selected_models": selected_models,
            "model_fallbacks": model_fallbacks,
            "available_models": available,
            "requests": budget.requests,
            "structured_output_probe": bool(args.probe_structured_output),
            "wire_schema_probe": bool(args.probe_wire_schemas),
            "wire_schema_diagnostics": wire_schema_diagnostics,
            "wire_schema_matrix": wire_schema_matrix,
        }

    result = await ResearchGraph(
        config=config,
        client=client,
        research_dir=args.research_dir,
        output_dir=args.output_dir,
    ).run()
    status = result["status"]
    telemetry = result["telemetry"]
    return {
        "ok": True,
        "run_id": status["run_id"],
        "execution_mode": status["execution_mode"],
        "publishable": status["publishable"],
        "market_memo_status": status["market_memo_status"],
        "stock_memos": status["stock_memos"],
        "eligible": len(status["universe"]["eligible"]),
        "selected": len(status["universe"]["selected"]),
        "excluded": len(status["universe"]["excluded"]),
        "requests": telemetry.requests,
        "cache_hits": telemetry.cache_hits,
        "billing_allowed": telemetry.billing_allowed,
        "estimated_cost": telemetry.estimated_cost,
        "free_tier_verified": status["free_tier_verified"],
        "selected_models": selected_models,
        "model_fallbacks": model_fallbacks,
        "available_models_count": len(available),
        "structured_output_probe": bool(args.probe_structured_output),
        "warnings": telemetry.warnings,
        "failed_nodes": {
            name: node.model_dump(mode="json")
            for name, node in telemetry.nodes.items()
            if node.status == "failed"
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except (CriticalGraphError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "public_artifacts_preserved": True,
                    "paid_fallback_attempted": False,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
