from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..schemas import RESEARCH_ARTIFACTS
from ..validators import validate_research_bundle, validate_safe_content
from .schemas import MarketMemo, RunTelemetry, StockMemo


class ResearchState:
    def __init__(self, root: Path):
        self.root = root
        self.artifacts: dict[str, dict[str, Any]] = {}
        for name in (*RESEARCH_ARTIFACTS, "research_manifest.json"):
            path = root / name
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"cannot load research artifact {name}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"research artifact {name} must be an object")
            self.artifacts[name] = value
        validation = validate_research_bundle(self.artifacts)
        if validation.errors:
            raise ValueError("invalid research state: " + "; ".join(validation.errors))

    @property
    def manifest(self) -> dict[str, Any]:
        return self.artifacts["research_manifest.json"]

    @property
    def stock_index(self) -> dict[str, Any]:
        return self.artifacts["stock_index.json"]

    def load_stock(self, ticker: str) -> dict[str, Any]:
        row = next(
            (item for item in self.stock_index.get("stocks", []) if item.get("ticker") == ticker),
            None,
        )
        if row is None:
            raise KeyError(ticker)
        relative = str(row.get("path") or "").removeprefix("data/research/")
        if not relative.startswith("stocks/"):
            raise ValueError(f"invalid stock path for {ticker}")
        name = relative
        if name not in self.artifacts:
            value = json.loads((self.root / relative).read_text(encoding="utf-8"))
            safety = validate_safe_content(name, value)
            if safety.errors:
                raise ValueError(f"unsafe stock artifact {ticker}: {'; '.join(safety.errors)}")
            self.artifacts[name] = value
        return self.artifacts[name]

    def stock_fingerprint(self, ticker: str) -> str:
        row = next(item for item in self.stock_index.get("stocks", []) if item.get("ticker") == ticker)
        return str(row.get("fingerprint") or "")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
        os.replace(temp, path)
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def validate_ai_output_dir(path: Path, *, require_real: bool = False) -> list[str]:
    errors: list[str] = []
    try:
        market_raw = json.loads((path / "market_memo.json").read_text(encoding="utf-8"))
        telemetry_raw = json.loads((path / "run_metadata.json").read_text(encoding="utf-8"))
        status = json.loads((path / "status.json").read_text(encoding="utf-8"))
        market = MarketMemo.model_validate(market_raw)
        telemetry = RunTelemetry.model_validate(telemetry_raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        return [f"invalid required AI artifact: {exc}"]
    for name, value in (
        ("market_memo.json", market_raw),
        ("run_metadata.json", telemetry_raw),
        ("status.json", status),
    ):
        errors.extend(validate_safe_content(name, value).errors)
    if market.publishable is not True or telemetry.publishable is not True or status.get("publishable") is not True:
        errors.append("required AI artifacts are not publishable")
    if status.get("verifier_executed") is not True:
        errors.append("verifier did not execute")
    if require_real and status.get("execution_mode") != "real":
        errors.append("mock AI artifact cannot be published as production research")
    rejected = set(status.get("rejected_finding_ids") or [])
    used = set(market.key_findings)
    for field in (
        "sector_context", "equity_context", "bond_context", "bank_context", "catalysts",
        "contradictions", "risks", "watch",
    ):
        used.update(getattr(market, field).finding_ids)
    if rejected & used:
        errors.append("rejected finding leaked into market memo")
    market_evidence_ids = {finding.id for finding in market.evidence}
    if not used.issubset(market_evidence_ids):
        errors.append("market memo references findings without embedded evidence")
    if not set(status.get("temporal_warnings") or []).issubset(set(market.data_quality)):
        errors.append("temporal warnings are not preserved in market memo")
    stock_dir = path / "stocks"
    if stock_dir.exists():
        for item in stock_dir.glob("*.json"):
            try:
                raw = json.loads(item.read_text(encoding="utf-8"))
                memo = StockMemo.model_validate(raw)
                errors.extend(validate_safe_content(f"stocks/{item.name}", raw).errors)
                if memo.publishable is not True:
                    errors.append(f"stock memo {item.name} is not publishable")
                used = _stock_finding_ids(memo)
                if rejected & used:
                    errors.append(f"rejected finding leaked into stock memo {item.name}")
                evidence_ids = {finding.id for finding in memo.evidence}
                if not used.issubset(evidence_ids):
                    errors.append(f"stock memo {item.name} references findings without embedded evidence")
            except (OSError, json.JSONDecodeError, ValidationError) as exc:
                errors.append(f"invalid stock memo {item.name}: {exc}")
    return sorted(set(errors))


def _stock_finding_ids(memo: StockMemo) -> set[str]:
    finding_ids = {finding.id for finding in memo.evidence}
    for field in (
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
    ):
        finding_ids.update(getattr(memo, field).finding_ids)
    return finding_ids
