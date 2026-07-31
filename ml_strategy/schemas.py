from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

SNAPSHOT_FILES = (
    "latest.json",
    "backtest.json",
    "model_card.json",
    "data_quality.json",
    "sector_features/latest_registry.json",
    "sector_features/latest_quality.json",
)
MODEL_STATUSES = {"production", "research_only", "rejected", "failed"}
DATA_STATUSES = {"pass", "degraded", "fail", "stale"}
SIGNAL_STATUSES = {"valid", "rejected", "no_signal", "stale", "solver_failed"}
ACTION_STATUSES = {"rebalance", "hold", "no_trade", "frozen"}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_latest(payload: dict) -> list[str]:
    errors: list[str] = []
    required = (
        "schema_version", "generated_at", "data_as_of", "run", "model_status", "data_status",
        "signal_status", "action_status", "signal", "published_portfolio", "candidate_portfolio",
        "execution", "portfolio", "model", "data_quality", "diagnostics",
    )
    errors.extend(f"latest: missing {key}" for key in required if key not in payload)
    try:
        datetime.fromisoformat(str(payload.get("generated_at", "")).replace("Z", "+00:00"))
    except ValueError:
        errors.append("latest: generated_at is not ISO-8601")
    if payload.get("model_status") not in MODEL_STATUSES:
        errors.append("latest: invalid model_status")
    if payload.get("data_status") not in DATA_STATUSES:
        errors.append("latest: invalid data_status")
    if payload.get("signal_status") not in SIGNAL_STATUSES:
        errors.append("latest: invalid signal_status")
    if payload.get("action_status") not in ACTION_STATUSES:
        errors.append("latest: invalid action_status")
    run = payload.get("run") or {}
    for key in (
        "run_id", "as_of", "calculated_at", "model_version", "artifact_hash", "universe_version",
        "features_version", "constraints_hash", "cost_model_version",
    ):
        if not run.get(key):
            errors.append(f"latest: run.{key} missing")
    published = payload.get("published_portfolio")
    positions = (published or {}).get("positions") if isinstance(published, dict) else []
    if positions:
        weights = [row.get("target_weight") for row in positions]
        if any(not _finite(weight) or weight < 0 for weight in weights):
            errors.append("latest: invalid target weights")
        cash = published.get("cash_weight")
        if not _finite(cash) or cash < 0:
            errors.append("latest: invalid cash weight")
        elif abs(sum(weights) + cash - 1.0) > 1e-5:
            errors.append("latest: positions plus cash do not sum to one")
    candidate = payload.get("candidate_portfolio") or {}
    if not isinstance(candidate.get("positions"), list):
        errors.append("latest: candidate positions missing")
    forbidden = {"shares", "target_rub", "trade_rub", "change_weight", "target_weight"}
    for row in candidate.get("positions") or []:
        leaked = forbidden.intersection(row)
        if leaked:
            errors.append(f"latest: candidate contains executable fields: {sorted(leaked)}")
            break
    action = payload.get("action_status")
    execution = payload.get("execution") or {}
    if action in {"no_trade", "frozen", "hold"}:
        if _finite(execution.get("turnover")) or _finite(execution.get("estimated_cost_rub")):
            errors.append("latest: non-actionable state contains executable turnover or costs")
    if action == "rebalance":
        if not positions:
            errors.append("latest: rebalance has no published positions")
        if not _finite(execution.get("turnover")) or not _finite(execution.get("estimated_cost_rub")):
            errors.append("latest: rebalance execution is incomplete")
    if payload.get("signal_status") != "valid" and published:
        current_run = run.get("run_id")
        if published.get("published_from_run_id") == current_run:
            errors.append("latest: rejected/non-valid candidate was published")
    return errors


def validate_backtest(payload: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload.get("folds"), list) or not payload["folds"]:
        errors.append("backtest: folds is empty")
    if not isinstance(payload.get("model_metrics"), dict):
        errors.append("backtest: model_metrics missing")
    if not isinstance(payload.get("portfolio_metrics"), dict):
        errors.append("backtest: portfolio_metrics missing")
    return errors


def validate_model_card(payload: dict) -> list[str]:
    errors: list[str] = []
    for key in ("champion", "challengers", "features", "target", "limitations"):
        if key not in payload:
            errors.append(f"model_card: missing {key}")
    if payload.get("target", {}).get("horizon_sessions") != 20:
        errors.append("model_card: target horizon must be 20 sessions")
    return errors


def validate_data_quality(payload: dict) -> list[str]:
    errors: list[str] = []
    if payload.get("status") not in {"PASS", "DEGRADED", "BLOCKED"}:
        errors.append("data_quality: invalid status")
    if not isinstance(payload.get("checks"), list):
        errors.append("data_quality: checks missing")
    if payload.get("production_data") != "real_sources_only":
        errors.append("data_quality: production data declaration missing")
    return errors


def validate_sector_registry(payload: dict) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("sector registry: invalid schema_version")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("sector registry: sources missing")
    else:
        for row in sources:
            if row.get("status") == "APPROVED" and (
                not row.get("provider") or not row.get("source_url")
            ):
                errors.append(f"sector registry: {row.get('series_id')} lacks provenance")
    return errors


def validate_sector_quality(payload: dict) -> list[str]:
    errors: list[str] = []
    if payload.get("status") not in {"PASS", "DEGRADED", "BLOCKED"}:
        errors.append("sector quality: invalid status")
    if payload.get("point_in_time_policy") != "available_at <= prediction_timestamp":
        errors.append("sector quality: point-in-time policy missing")
    packs = payload.get("packs")
    if not isinstance(packs, list) or len(packs) != 4:
        errors.append("sector quality: four priority packs required")
    elif any(row.get("status") not in {"APPROVED", "RESEARCH_ONLY", "BLOCKED"} for row in packs):
        errors.append("sector quality: invalid pack status")
    return errors


def validate_bundle(directory: str | os.PathLike[str]) -> list[str]:
    root = Path(directory)
    errors: list[str] = []
    validators = {
        "latest.json": validate_latest,
        "backtest.json": validate_backtest,
        "model_card.json": validate_model_card,
        "data_quality.json": validate_data_quality,
        "sector_features/latest_registry.json": validate_sector_registry,
        "sector_features/latest_quality.json": validate_sector_quality,
    }
    for name, validator in validators.items():
        path = root / name
        if not path.exists():
            errors.append(f"missing {name}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{name}: invalid JSON ({exc})")
            continue
        errors.extend(validator(payload))
    return errors


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def publish_bundle(bundle: dict[str, dict], data_root: Path, site_root: Path, history_date: str) -> None:
    with tempfile.TemporaryDirectory(prefix="ml-strategy-") as tmp:
        stage = Path(tmp)
        for name in SNAPSHOT_FILES:
            write_json(stage / name, bundle[name])
        errors = validate_bundle(stage)
        if errors:
            raise ValueError("snapshot validation failed: " + "; ".join(errors))
        for root in (data_root, site_root):
            root.mkdir(parents=True, exist_ok=True)
            for name in SNAPSHOT_FILES:
                write_json(root / name, bundle[name])
        write_json(data_root / "history" / f"{history_date}.json", bundle["latest.json"])
        write_json(site_root / "history" / f"{history_date}.json", bundle["latest.json"])
