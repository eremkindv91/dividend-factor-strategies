from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from ..validators import validate_safe_content
from .client import AIClient
from .config import AIConfig
from .prompts import system_prompt
from .schemas import AnalystOutput, RequestUsage


@dataclass(frozen=True)
class AnalystTask:
    name: str
    artifacts: dict[str, dict[str, Any]]
    critical: bool = False


@dataclass(frozen=True)
class AnalystResult:
    name: str
    output: AnalystOutput | None
    usage: RequestUsage | None
    error: Exception | None
    duration_ms: int
    critical: bool


def committee_tasks(artifacts: dict[str, dict[str, Any]], manifest: dict[str, Any]) -> list[AnalystTask]:
    market = artifacts["market_snapshot.json"]
    macro = {
        key: market.get(key)
        for key in ("schema_version", "component", "asof", "source_dates", "rates", "fx", "data_quality")
    }
    return [
        AnalystTask("market", {"market_snapshot.json": market}, critical=True),
        AnalystTask("macro", {"market_snapshot.json": macro}),
        AnalystTask("equity", {"sector_snapshot.json": artifacts["sector_snapshot.json"]}),
        AnalystTask("bonds", {"bond_snapshot.json": artifacts["bond_snapshot.json"]}),
        AnalystTask("banks", {"bank_snapshot.json": artifacts["bank_snapshot.json"]}),
        AnalystTask("news", {"news_snapshot.json": artifacts["news_snapshot.json"]}),
    ]


def stock_task(
    ticker: str,
    stock: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
) -> AnalystTask:
    stock_artifacts = {
        f"stocks/{ticker}.json": stock,
        "market_snapshot.json": artifacts["market_snapshot.json"],
        "sector_snapshot.json": artifacts["sector_snapshot.json"],
        "news_snapshot.json": artifacts["news_snapshot.json"],
    }
    bank = next(
        (
            row
            for row in artifacts["bank_snapshot.json"].get("banks", [])
            if str(row.get("ticker") or "").upper() == ticker
        ),
        None,
    )
    if bank is not None:
        stock_artifacts[f"stock_context/{ticker}_bank.json"] = {
            "schema_version": artifacts["bank_snapshot.json"].get("schema_version"),
            "component": "bank_context",
            "asof": artifacts["bank_snapshot.json"].get("asof"),
            "bank": bank,
            "data_quality": artifacts["bank_snapshot.json"].get("data_quality") or {},
        }
    return AnalystTask(
        f"stock:{ticker}",
        stock_artifacts,
        critical=True,
    )


async def run_analyst(
    task: AnalystTask,
    *,
    client: AIClient,
    config: AIConfig,
    manifest: dict[str, Any],
) -> AnalystResult:
    started = time.perf_counter()
    payload = {
        "graph_version": config.graph_version,
        "analyst": "stock" if task.name.startswith("stock:") else task.name,
        "temporal": {
            "research_asof": manifest.get("research_asof"),
            "component_eligibility": manifest.get("component_eligibility") or {},
            "temporal_warnings": manifest.get("temporal_warnings") or [],
            "survivorship_status": manifest.get("survivorship_status"),
        },
        "artifacts": task.artifacts,
    }
    safety = validate_safe_content(f"ai_payload:{task.name}", payload)
    if safety.errors:
        return AnalystResult(
            task.name, None, None, ValueError("; ".join(safety.errors)),
            round((time.perf_counter() - started) * 1000), task.critical,
        )
    role = "stock" if task.name.startswith("stock:") else task.name
    try:
        generated = await client.generate(
            node=task.name,
            system_prompt=system_prompt(role),
            payload=payload,
            response_model=AnalystOutput,
            model=config.analyst_model,
        )
        if generated.value.analyst != role:
            raise ValueError(f"analyst identity mismatch: expected {role}, got {generated.value.analyst}")
        return AnalystResult(
            task.name, generated.value, generated.usage, None,
            round((time.perf_counter() - started) * 1000), task.critical,
        )
    except Exception as exc:  # noqa: BLE001 - failure-domain boundary
        return AnalystResult(
            task.name, None, None, exc,
            round((time.perf_counter() - started) * 1000), task.critical,
        )


async def run_parallel_analysts(
    tasks: list[AnalystTask],
    *,
    client: AIClient,
    config: AIConfig,
    manifest: dict[str, Any],
) -> list[AnalystResult]:
    return list(
        await asyncio.gather(
            *(run_analyst(task, client=client, config=config, manifest=manifest) for task in tasks)
        )
    )
