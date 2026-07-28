from __future__ import annotations

import json
import math
import os
import re
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
SIGNALS = {
    "NO_ACTION",
    "WATCH",
    "REBALANCE",
    "RISK_OFF",
    "DATA_STALE",
    "MODEL_UNCERTAIN",
    "DEGRADED",
}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_latest(payload: dict) -> list[str]:
    errors: list[str] = []
    required = (
        "schema_version",
        "generated_at",
        "data_as_of",
        "signal",
        "portfolio",
        "model",
        "data_quality",
        "execution_policy",
    )
    errors.extend(f"latest: missing {key}" for key in required if key not in payload)
    try:
        datetime.fromisoformat(str(payload.get("generated_at", "")).replace("Z", "+00:00"))
    except ValueError:
        errors.append("latest: generated_at is not ISO-8601")
    if payload.get("signal", {}).get("action") not in SIGNALS:
        errors.append("latest: invalid signal action")
    positions = payload.get("portfolio", {}).get("positions")
    if not isinstance(positions, list) or not positions:
        errors.append("latest: portfolio.positions is empty")
    else:
        weights = [row.get("target_weight") for row in positions]
        if any(not _finite(weight) or weight < 0 for weight in weights):
            errors.append("latest: invalid target weights")
        cash = payload.get("portfolio", {}).get("cash_weight")
        if not _finite(cash) or cash < 0:
            errors.append("latest: invalid cash weight")
        elif abs(sum(weights) + cash - 1.0) > 1e-5:
            errors.append("latest: positions plus cash do not sum to one")
        diagnostics = payload.get("portfolio", {}).get("diagnostics", {})
        if not isinstance(diagnostics, dict):
            errors.append("latest: portfolio diagnostics missing")
        elif diagnostics.get("cash", {}).get("is_market_timing_signal") is not False:
            errors.append("latest: cash must not be presented as a market-timing signal")
        else:
            sorted_weights = sorted(weights, reverse=True)
            if diagnostics.get("positions_count") != len(positions):
                errors.append("latest: diagnostics positions_count mismatch")
            if not _finite(diagnostics.get("top5_weight")) or abs(
                diagnostics["top5_weight"] - sum(sorted_weights[:5])
            ) > 1e-5:
                errors.append("latest: diagnostics top5_weight mismatch")
            if not _finite(diagnostics.get("largest_position_weight")) or abs(
                diagnostics["largest_position_weight"] - sorted_weights[0]
            ) > 1e-5:
                errors.append("latest: diagnostics largest_position_weight mismatch")
            if diagnostics.get("cash", {}).get("weight") != cash:
                errors.append("latest: diagnostics cash weight mismatch")
    execution = payload.get("execution_policy", {})
    if execution.get("status") not in {
        "BLOCKED",
        "RESEARCH_ONLY",
        "MODEL_PORTFOLIO_READY",
    }:
        errors.append("latest: invalid execution policy status")
    if execution.get("auto_execution_allowed") is not False:
        errors.append("latest: automatic execution must be disabled")
    if execution.get("uses_user_holdings") is not False:
        errors.append("latest: model snapshot must not claim to use user holdings")
    ready = execution.get("status") == "MODEL_PORTFOLIO_READY"
    if execution.get("model_portfolio_ready") is not ready:
        errors.append("latest: model portfolio readiness mismatch")
    if execution.get("manual_rebalance_plan_available") is not ready:
        errors.append("latest: manual rebalance availability mismatch")
    if not execution.get("reason"):
        errors.append("latest: execution policy reason missing")
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


def validate_ledger_index(payload: dict) -> list[str]:
    from .ledger import validate_ledger

    return validate_ledger(payload)


def validate_advanced_models(payload: dict) -> list[str]:
    errors: list[str] = []
    taxonomy = {
        "NOT_IMPLEMENTED",
        "IMPLEMENTED_NOT_EVALUATED",
        "EVALUATED_REJECTED",
        "EVALUATED_APPROVED",
        "PRODUCTION_CHAMPION",
        "EXECUTION_FAILED",
    }
    if payload.get("execution_mode") != "production_evaluation":
        errors.append("advanced_models: execution_mode must be production_evaluation")
    models = payload.get("models")
    if not isinstance(models, dict) or set(models) != {
        "elastic_net",
        "elastic_net_iceemdan",
        "patchtst",
    }:
        errors.append("advanced_models: three model records required")
        return errors
    for name, row in models.items():
        if row.get("status") not in taxonomy:
            errors.append(f"advanced_models: {name} has invalid status")
        execution = row.get("execution", {})
        for key in (
            "execution_mode",
            "trained",
            "backend",
            "checkpoint",
            "folds",
            "prediction_count",
            "common_test_window",
        ):
            if key not in execution:
                errors.append(f"advanced_models: {name} missing execution.{key}")
        if execution.get("trained") is not True:
            errors.append(f"advanced_models: {name} trained=false")
        if execution.get("mock_backend") is not False:
            errors.append(f"advanced_models: {name} mock backend")
        if execution.get("checkpoint_exists") is not True:
            errors.append(f"advanced_models: {name} checkpoint missing")
        if not isinstance(execution.get("prediction_count"), int) or execution["prediction_count"] <= 0:
            errors.append(f"advanced_models: {name} predictions empty")
    common = payload.get("common_test_window", {})
    if (
        not common.get("identical_rows")
        or not common.get("identical_targets")
        or not common.get("rows")
    ):
        errors.append("advanced_models: common test window integrity failed")
    integrity = payload.get("comparison_integrity", {})
    for key in (
        "same_universe_rows",
        "same_targets",
        "same_rebalance_dates",
        "same_transaction_costs",
        "same_optimizer_constraints",
    ):
        if integrity.get(key) is not True:
            errors.append(f"advanced_models: comparison_integrity.{key} failed")
    return errors


def validate_public_advanced_models(payload: dict) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 2:
        errors.append("public advanced_models: schema_version must be 2")
    if not payload.get("generated_at"):
        errors.append("public advanced_models: generated_at missing")
    window = payload.get("evaluation_window", {})
    if not window.get("common_oos_start") or not window.get("common_oos_end"):
        errors.append("public advanced_models: evaluation window missing")
    if not isinstance(window.get("folds"), int) or window["folds"] <= 0:
        errors.append("public advanced_models: folds missing")
    if not isinstance(window.get("oos_rows"), int) or window["oos_rows"] <= 0:
        errors.append("public advanced_models: OOS rows missing")
    governance = payload.get("production_governance", {})
    if governance.get("production_model") != "ElasticNet":
        errors.append("public advanced_models: production model must remain ElasticNet")
    if governance.get("challengers_can_switch_production_automatically") is not False:
        errors.append("public advanced_models: automatic challenger promotion is forbidden")
    models = payload.get("models")
    if not isinstance(models, list) or len(models) != 3:
        errors.append("public advanced_models: exactly three model rows required")
        models = []
    production = [row for row in models if row.get("role") == "production"]
    if len(production) != 1 or production[0].get("model_id") != "elastic_net":
        errors.append("public advanced_models: ElasticNet must be the only production model")
    for row in models:
        model_id = row.get("model_id", "unknown")
        if row.get("trained") is not True or row.get("evaluated") is not True:
            errors.append(f"public advanced_models: {model_id} lacks real evaluation evidence")
        expected_affects = model_id == "elastic_net"
        if row.get("affects_current_portfolio") is not expected_affects:
            errors.append(
                f"public advanced_models: {model_id} affects_current_portfolio is invalid"
            )
        if model_id != "elastic_net" and row.get("status") != "EVALUATED_REJECTED":
            errors.append(f"public advanced_models: {model_id} must remain rejected")
        for key in ("rank_ic", "icir", "cagr_net", "sharpe_net", "max_drawdown"):
            if not _finite(row.get(key)):
                errors.append(f"public advanced_models: {model_id}.{key} is not finite")
    sector = payload.get("sector_packs", {})
    if sector.get("affects_current_portfolio") is not False:
        errors.append("public advanced_models: sector packs must not affect the portfolio")
    integrity = payload.get("integrity", {})
    for key in (
        "same_universe",
        "same_targets",
        "same_rebalance_dates",
        "same_transaction_costs",
        "same_optimizer_constraints",
        "production_model_unchanged",
    ):
        if integrity.get(key) is not True:
            errors.append(f"public advanced_models: integrity.{key} failed")
    if integrity.get("mock_backends_used") is not False:
        errors.append("public advanced_models: mock backend evidence is forbidden")

    forbidden_keys = {
        "checkpoint",
        "checkpoint_sha256",
        "checkpoints",
        "fold_records",
        "training_history",
        "artifacts",
        "predictions",
        "dataset",
    }
    path_pattern = re.compile(r"(^|[\"'\s])(?:/tmp/|/private/|/Users/|[A-Za-z]:\\\\)")
    secret_pattern = re.compile(
        r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
        r"(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S+)",
        re.IGNORECASE,
    )

    def inspect(value: Any, location: str = "root") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key.lower() in forbidden_keys:
                    errors.append(f"public advanced_models: forbidden key {location}.{key}")
                inspect(nested, f"{location}.{key}")
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                inspect(nested, f"{location}[{index}]")
        elif isinstance(value, str):
            if path_pattern.search(value):
                errors.append(f"public advanced_models: machine path at {location}")
            if secret_pattern.search(value):
                errors.append(f"public advanced_models: possible secret at {location}")

    inspect(payload)
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
        "ledger/index.json": validate_ledger_index,
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
    advanced_path = root / "advanced_models.json"
    if advanced_path.exists():
        try:
            advanced_payload = json.loads(advanced_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"advanced_models.json: invalid JSON ({exc})")
        else:
            errors.extend(validate_public_advanced_models(advanced_payload))
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
        for name, payload in bundle.items():
            write_json(stage / name, payload)
        errors = validate_bundle(stage)
        if errors:
            raise ValueError("snapshot validation failed: " + "; ".join(errors))
        for root in (data_root, site_root):
            root.mkdir(parents=True, exist_ok=True)
            for name, payload in bundle.items():
                write_json(root / name, payload)
        write_json(data_root / "history" / f"{history_date}.json", bundle["latest.json"])
        write_json(site_root / "history" / f"{history_date}.json", bundle["latest.json"])
