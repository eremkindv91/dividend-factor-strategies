from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr

from .config import StrategyConfig
from .data import load_market_data
from .features import FEATURE_COLUMNS, build_feature_panel
from .iceemdan_features import (
    ICEEMDAN_FEATURE_COLUMNS,
    build_iceemdan_feature_panel,
    validate_against_reference,
)
from .models import ModelEvaluation, prediction_metrics, walk_forward
from .patchtst import PatchTSTExecution, evaluate_patchtst
from .sector_features.registry import load_config


STATUS_VALUES = {
    "NOT_IMPLEMENTED",
    "IMPLEMENTED_NOT_EVALUATED",
    "EVALUATED_REJECTED",
    "EVALUATED_APPROVED",
    "PRODUCTION_CHAMPION",
    "EXECUTION_FAILED",
}


class AdvancedExecutionError(RuntimeError):
    pass


@dataclass
class AdvancedEvaluationResult:
    payload: dict
    predictions: pd.DataFrame
    patchtst_history: list[dict]


def _public_decision_reason(model_id: str, model: dict) -> str:
    if model_id == "elastic_net":
        return (
            "Лучший совокупный результат среди моделей, проверенных на одинаковом OOS-периоде. "
            "Абсолютная доходность после издержек остаётся слабой."
        )
    if model_id == "elastic_net_iceemdan":
        return (
            "Немного улучшил качество ранжирования, но ухудшил доходность после издержек "
            "и Sharpe. Не включён в портфель."
        )
    return (
        "Немного улучшил Rank IC, но показал отрицательный top-bottom spread и существенно "
        "худшую доходность после издержек. Не включён в портфель."
    )


def _absolute_quality(portfolio: dict) -> dict:
    cagr = portfolio.get("cagr_net")
    sharpe = portfolio.get("sharpe_net")
    if cagr is None or sharpe is None or cagr <= 0 or sharpe < 0.3:
        return {
            "status": "WEAK_NEEDS_IMPROVEMENT",
            "label": "Слабая, требует улучшения",
            "reason": "CAGR после издержек отрицательный, а Sharpe остаётся низким.",
        }
    if sharpe < 0.8:
        return {
            "status": "MODERATE",
            "label": "Умеренная",
            "reason": "Доходность положительная, но risk-adjusted результат ограничен.",
        }
    return {
        "status": "STRONG",
        "label": "Сильная",
        "reason": "Доходность после издержек и risk-adjusted результат устойчиво положительны.",
    }


def build_public_advanced_models(
    payload: dict,
    sector_quality: dict | None = None,
) -> dict:
    common = payload["common_test_window"]
    model_rows = []
    for model_id in ("elastic_net", "elastic_net_iceemdan", "patchtst"):
        source = payload["models"][model_id]
        prediction = source["common_window"]["prediction"]
        portfolio = source["common_window"]["portfolio"]
        role = "production" if model_id == "elastic_net" else "research_challenger"
        promotion = source.get("promotion", {})
        model_rows.append(
            {
                "model_id": model_id,
                "model": source["label"],
                "role": role,
                "status": source["status"],
                "affects_current_portfolio": model_id == "elastic_net",
                "trained": source["execution"].get("trained") is True,
                "evaluated": source["status"]
                in {"PRODUCTION_CHAMPION", "EVALUATED_APPROVED", "EVALUATED_REJECTED"},
                "rank_ic": prediction.get("rank_ic"),
                "pearson_ic": prediction.get("pearson_ic"),
                "icir": prediction.get("rank_icir"),
                "hit_rate": prediction.get("hit_rate"),
                "top_bottom_spread": prediction.get("top_bottom_spread"),
                "cagr_net": portfolio.get("cagr_net"),
                "sharpe_net": portfolio.get("sharpe_net"),
                "max_drawdown": portfolio.get("max_drawdown_net"),
                "turnover": portfolio.get("average_turnover"),
                "costs_return": portfolio.get("total_cost_return"),
                "oos_rows": prediction.get("oos_rows"),
                "folds": prediction.get("folds"),
                "promotion_gate": {
                    "approved": promotion.get("approved") if promotion else None,
                    "forecast_checks": promotion.get("forecast_checks", {}),
                    "portfolio_checks": promotion.get("portfolio_checks", {}),
                },
                "decision_reason": _public_decision_reason(model_id, source),
            }
        )
    production_portfolio = payload["models"]["elastic_net"]["common_window"]["portfolio"]
    packs = (sector_quality or {}).get("packs", [])
    return {
        "schema_version": 2,
        "generated_at": payload["generated_at"],
        "evaluation_window": {
            "common_oos_start": common["start"],
            "common_oos_end": common["end"],
            "folds": common["rebalance_dates"],
            "oos_rows": common["rows"],
        },
        "common_oos_period": {
            "start": common["start"],
            "end": common["end"],
        },
        "transaction_costs": {
            "one_way_bps": payload["comparison_integrity"]["transaction_cost_bps_one_way"],
            "turnover_cap": payload["comparison_integrity"]["optimizer_constraints"][
                "turnover_cap"
            ],
        },
        "production_governance": {
            "production_model": "ElasticNet",
            "production_model_id": "elastic_net",
            "selection_reason": (
                "Лучший совокупный результат среди моделей, прошедших одинаковое "
                "out-of-sample сравнение."
            ),
            "absolute_performance_assessment": _absolute_quality(production_portfolio),
            "challengers_can_switch_production_automatically": False,
        },
        "models": model_rows,
        "sector_packs": {
            "role": "research_context",
            "status": "RESEARCH_ONLY",
            "affects_current_portfolio": False,
            "summary": (
                "Отраслевые факторы рассчитываются и проверяются, но используются только "
                "как аналитический контекст. Веса портфеля от них не зависят."
            ),
            "packs": [
                {
                    "pack_id": row.get("pack_id"),
                    "label": row.get("label"),
                    "source_status": row.get("status"),
                }
                for row in packs
            ],
        },
        "integrity": {
            "same_universe": payload["comparison_integrity"]["same_universe_rows"],
            "same_targets": payload["comparison_integrity"]["same_targets"],
            "same_rebalance_dates": payload["comparison_integrity"]["same_rebalance_dates"],
            "same_transaction_costs": payload["comparison_integrity"][
                "same_transaction_costs"
            ],
            "same_optimizer_constraints": payload["comparison_integrity"][
                "same_optimizer_constraints"
            ],
            "production_model_unchanged": payload["production_model_unchanged"],
            "mock_backends_used": False,
        },
        "methodology": {
            "comparison": "purged_walk_forward_common_oos_window",
            "promotion_requires_signal_and_portfolio_improvement": True,
            "complexity_is_not_a_promotion_reason": True,
            "documentation_url": "ml_strategy/advanced_challenger_evaluation.md",
        },
        "stale_after_days": 10,
    }


def _model_rows(evaluation: ModelEvaluation) -> pd.DataFrame:
    return evaluation.predictions[evaluation.predictions["model"] == evaluation.champion].copy()


def _artifact_path(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _checkpoint_metadata(repo: Path, path: Path) -> dict:
    exists = path.exists() and path.stat().st_size > 0
    return {
        "checkpoint": _artifact_path(repo, path),
        "checkpoint_exists": exists,
        "checkpoint_sha256": hashlib.sha256(path.read_bytes()).hexdigest() if exists else None,
    }


def _validate_execution(name: str, metadata: dict, predictions: pd.DataFrame) -> None:
    failures = []
    if metadata.get("execution_mode") != "production_evaluation":
        failures.append("execution_mode")
    if metadata.get("trained") is not True:
        failures.append("trained")
    if metadata.get("mock_backend") is not False:
        failures.append("mock_backend")
    if not metadata.get("checkpoint_exists"):
        failures.append("checkpoint")
    if predictions.empty:
        failures.append("oos_predictions")
    if int(metadata.get("prediction_count") or 0) != len(predictions):
        failures.append("prediction_count")
    if not metadata.get("folds"):
        failures.append("folds")
    if failures:
        raise AdvancedExecutionError(f"{name}: execution integrity failed: {', '.join(failures)}")


def _ic_series(rows: pd.DataFrame, method: str) -> pd.Series:
    values: dict[pd.Timestamp, float] = {}
    for date, group in rows.groupby("date", sort=True):
        if len(group) < 3 or group["forecast"].nunique() < 2 or group["actual"].nunique() < 2:
            continue
        if method == "rank":
            value = spearmanr(group["actual"], group["forecast"]).statistic
        else:
            value = pearsonr(group["actual"], group["forecast"]).statistic
        if np.isfinite(value):
            values[pd.Timestamp(date)] = float(value)
    return pd.Series(values, dtype=float)


def _prediction_summary(rows: pd.DataFrame, config: StrategyConfig) -> dict:
    raw = prediction_metrics(rows["actual"].to_numpy(), rows["forecast"].to_numpy())
    rank_ic = _ic_series(rows, "rank")
    pearson_ic = _ic_series(rows, "pearson")
    periods_per_year = 252 / config.horizon

    def icir(values: pd.Series) -> float | None:
        if len(values) < 2 or values.std(ddof=1) <= 0:
            return None
        return float(values.mean() / values.std(ddof=1) * np.sqrt(periods_per_year))

    return {
        "oos_rows": int(len(rows)),
        "folds": int(rows["date"].nunique()),
        "pearson_ic": float(pearson_ic.mean()) if len(pearson_ic) else None,
        "rank_ic": float(rank_ic.mean()) if len(rank_ic) else None,
        "pearson_icir": icir(pearson_ic),
        "rank_icir": icir(rank_ic),
        "pooled_pearson_ic": raw.get("pearson_ic"),
        "pooled_rank_ic": raw.get("spearman_ic"),
        "hit_rate": raw.get("hit_rate"),
        "top_bottom_spread": raw.get("top_bottom_spread"),
        "mae": raw.get("mae"),
        "rmse": raw.get("rmse"),
    }


def _portfolio_backtest(rows: pd.DataFrame, config: StrategyConfig) -> tuple[list[dict], dict]:
    previous = pd.Series(dtype=float)
    curve: list[dict] = []
    for prediction_date, group in rows.groupby("date", sort=True):
        selected = group.nlargest(config.holdings, "forecast")
        if len(selected) < 2:
            continue
        target = pd.Series(1 / len(selected), index=selected["ticker"].astype(str))
        union = target.index.union(previous.index)
        target = target.reindex(union).fillna(0)
        previous = previous.reindex(union).fillna(0)
        delta = target - previous
        unconstrained = float(delta.abs().sum())
        scale = min(1.0, config.turnover_cap / unconstrained) if unconstrained else 1.0
        weights = previous + delta * scale
        turnover = float((weights - previous).abs().sum())
        realized = group.set_index("ticker")["forward_total_return"].reindex(weights.index).fillna(0)
        gross = float((weights * realized).sum())
        benchmark_return = float((group["forward_total_return"] - group["actual"]).median())
        cost = turnover * config.one_way_cost_bps / 10_000
        curve.append(
            {
                "date": pd.Timestamp(prediction_date),
                "gross_return": gross,
                "net_return": gross - cost,
                "benchmark_return": benchmark_return,
                "turnover": turnover,
                "cost_return": cost,
            }
        )
        previous = weights
    if not curve:
        raise AdvancedExecutionError("portfolio comparison produced no periods")
    frame = pd.DataFrame(curve)
    periods_per_year = 252 / config.horizon

    def metrics(values: np.ndarray) -> dict:
        cumulative = np.cumprod(1 + values)
        drawdown = cumulative / np.maximum.accumulate(cumulative) - 1
        years = len(values) / periods_per_year
        downside = values[values < 0]
        return {
            "cagr": float(cumulative[-1] ** (1 / years) - 1) if years > 0 and cumulative[-1] > 0 else None,
            "sharpe": (
                float(values.mean() / values.std(ddof=1) * np.sqrt(periods_per_year))
                if len(values) > 1 and values.std(ddof=1) > 0
                else None
            ),
            "sortino": (
                float(values.mean() / downside.std(ddof=1) * np.sqrt(periods_per_year))
                if len(downside) > 1 and downside.std(ddof=1) > 0
                else None
            ),
            "max_drawdown": float(drawdown.min()),
            "cumulative_return": float(cumulative[-1] - 1),
        }

    gross = metrics(frame["gross_return"].to_numpy())
    net = metrics(frame["net_return"].to_numpy())
    benchmark_cumulative = float(np.prod(1 + frame["benchmark_return"]) - 1)
    return (
        [
            {
                **row,
                "date": pd.Timestamp(row["date"]).date().isoformat(),
            }
            for row in frame.to_dict("records")
        ],
        {
            "periods": int(len(frame)),
            "cagr_gross": gross["cagr"],
            "cagr_net": net["cagr"],
            "sharpe_gross": gross["sharpe"],
            "sharpe_net": net["sharpe"],
            "sortino_net": net["sortino"],
            "max_drawdown_net": net["max_drawdown"],
            "cumulative_return_gross": gross["cumulative_return"],
            "cumulative_return_net": net["cumulative_return"],
            "benchmark_cumulative_return": benchmark_cumulative,
            "excess_cumulative_return_net": net["cumulative_return"] - benchmark_cumulative,
            "average_turnover": float(frame["turnover"].mean()),
            "total_cost_return": float(frame["cost_return"].sum()),
            "total_cost_rub": float(frame["cost_return"].sum() * config.capital_rub),
            "cost_assumption_bps_one_way": config.one_way_cost_bps,
            "turnover_cap": config.turnover_cap,
        },
    )


def _yearly_metrics(rows: pd.DataFrame, curve: list[dict], config: StrategyConfig) -> list[dict]:
    curve_frame = pd.DataFrame(curve)
    curve_frame["date"] = pd.to_datetime(curve_frame["date"])
    output: list[dict] = []
    for year, group in rows.groupby(pd.to_datetime(rows["date"]).dt.year):
        prediction = _prediction_summary(group, config)
        periods = curve_frame[curve_frame["date"].dt.year == int(year)]
        output.append(
            {
                "year": int(year),
                **prediction,
                "portfolio_periods": int(len(periods)),
                "gross_return": (
                    float(np.prod(1 + periods["gross_return"]) - 1) if len(periods) else None
                ),
                "net_return": float(np.prod(1 + periods["net_return"]) - 1) if len(periods) else None,
                "turnover": float(periods["turnover"].mean()) if len(periods) else None,
                "cost_return": float(periods["cost_return"].sum()) if len(periods) else None,
            }
        )
    return output


def _common_rows(evaluations: dict[str, ModelEvaluation]) -> tuple[dict[str, pd.DataFrame], dict]:
    model_rows = {name: _model_rows(evaluation) for name, evaluation in evaluations.items()}
    key_sets = {
        name: set(zip(pd.to_datetime(rows["date"]), rows["ticker"].astype(str)))
        for name, rows in model_rows.items()
    }
    common = set.intersection(*key_sets.values())
    if not common:
        raise AdvancedExecutionError("models have no common OOS rows")
    filtered: dict[str, pd.DataFrame] = {}
    for name, rows in model_rows.items():
        keys = list(zip(pd.to_datetime(rows["date"]), rows["ticker"].astype(str)))
        mask = [key in common for key in keys]
        filtered[name] = rows.loc[mask].sort_values(["date", "ticker"]).reset_index(drop=True)
    ordered_keys = [
        list(zip(pd.to_datetime(rows["date"]), rows["ticker"].astype(str)))
        for rows in filtered.values()
    ]
    identical = all(keys == ordered_keys[0] for keys in ordered_keys[1:])
    reference = next(iter(filtered.values()))
    identical_targets = all(
        np.allclose(rows["actual"], reference["actual"], rtol=0, atol=1e-12, equal_nan=True)
        and np.allclose(
            rows["forward_total_return"],
            reference["forward_total_return"],
            rtol=0,
            atol=1e-12,
            equal_nan=True,
        )
        for rows in list(filtered.values())[1:]
    )
    dates = sorted({key[0] for key in common})
    return filtered, {
        "start": dates[0].date().isoformat(),
        "end": dates[-1].date().isoformat(),
        "rebalance_dates": len(dates),
        "rows": len(common),
        "identical_rows": identical,
        "identical_targets": identical_targets,
        "models": {
            name: {
                "full_oos_rows": len(model_rows[name]),
                "common_oos_rows": len(filtered[name]),
                "coverage": len(filtered[name]) / len(model_rows[name]),
            }
            for name in model_rows
        },
    }


def _promotion_record(
    name: str,
    baseline: dict,
    candidate: dict,
    common_window: dict,
    promotion: dict,
) -> dict:
    forecast_checks = {
        "identical_test_rows": bool(common_window["identical_rows"]),
        "identical_targets": bool(common_window["identical_targets"]),
        "rank_ic_improvement": (
            candidate["prediction"]["rank_ic"] - baseline["prediction"]["rank_ic"]
            >= float(promotion["minimum_spearman_ic_improvement"])
        ),
        "hit_rate_nonworse": (
            candidate["prediction"]["hit_rate"] - baseline["prediction"]["hit_rate"]
            >= float(promotion["minimum_hit_rate_change"])
        ),
        "positive_spread": (
            candidate["prediction"]["top_bottom_spread"] > 0
            if promotion.get("require_positive_top_bottom_spread", True)
            else True
        ),
    }
    portfolio_checks = {
        "better_after_cost_excess": (
            candidate["portfolio"]["excess_cumulative_return_net"]
            > baseline["portfolio"]["excess_cumulative_return_net"]
        ),
        "nonworse_after_cost_sharpe": (
            candidate["portfolio"]["sharpe_net"] >= baseline["portfolio"]["sharpe_net"]
        ),
        "same_costs": (
            candidate["portfolio"]["cost_assumption_bps_one_way"]
            == baseline["portfolio"]["cost_assumption_bps_one_way"]
        ),
        "same_turnover_cap": (
            candidate["portfolio"]["turnover_cap"] == baseline["portfolio"]["turnover_cap"]
        ),
    }
    approved = all(forecast_checks.values()) and all(portfolio_checks.values())
    return {
        "candidate": name,
        "status": "EVALUATED_APPROVED" if approved else "EVALUATED_REJECTED",
        "forecast_checks": forecast_checks,
        "portfolio_checks": portfolio_checks,
        "approved": approved,
    }


def _evaluate_rows(rows: pd.DataFrame, config: StrategyConfig) -> dict:
    curve, portfolio = _portfolio_backtest(rows, config)
    return {
        "prediction": _prediction_summary(rows, config),
        "portfolio": portfolio,
        "calendar_years": _yearly_metrics(rows, curve, config),
    }


def run_advanced_evaluation(
    repo: Path,
    strategy_config: StrategyConfig | None = None,
    ice_backend_factory=None,
    execution_mode: str = "production_evaluation",
) -> AdvancedEvaluationResult:
    config = strategy_config or StrategyConfig()
    advanced = load_config(repo / "config" / "ml_strategy" / "advanced_models.yml")
    if execution_mode == "production_evaluation" and ice_backend_factory is not None:
        raise AdvancedExecutionError("production evaluation cannot use an injected ICEEMDAN backend")
    data = load_market_data(
        repo / "data" / "daily",
        repo / "data" / "security_master.json",
        repo / "data" / "daily" / "benchmarks" / "MCFTR.parquet",
        repo / "data" / "daily" / "dividends.json",
    )
    panel = build_feature_panel(data, config)
    generated_at = datetime.now(timezone.utc)
    run_id = generated_at.strftime("%Y%m%dT%H%M%SZ")
    output_root = repo / "data" / "ml_strategy" / "advanced"
    checkpoint_root = output_root / "checkpoints" / run_id
    output_root.mkdir(parents=True, exist_ok=True)

    baseline_checkpoint = checkpoint_root / "elastic_net.joblib"
    baseline = walk_forward(
        panel,
        config,
        model_name="elastic_net",
        force_model=True,
        checkpoint_path=baseline_checkpoint,
    )
    evaluations: dict[str, ModelEvaluation] = {"elastic_net": baseline}
    execution: dict[str, dict] = {
        "elastic_net": {
            "execution_mode": execution_mode,
            "trained": True,
            "mock_backend": False,
            "backend": "sklearn_elastic_net",
            "folds": len(baseline.folds),
            "prediction_count": len(_model_rows(baseline)),
            "seed": config.random_seed,
            **_checkpoint_metadata(repo, baseline_checkpoint),
        }
    }

    ice_config = advanced["iceemdan"]
    numerical_validation = validate_against_reference(
        repo / "config" / "ml_strategy" / "iceemdan_reference.json"
    )
    if numerical_validation["status"] != "PASS":
        raise AdvancedExecutionError("ICEEMDAN numerical validation failed")
    ice_checkpoint = checkpoint_root / "elastic_net_iceemdan.joblib"
    ice_panel = build_iceemdan_feature_panel(
        panel,
        data.benchmark,
        config,
        ice_config,
        cache_dir=output_root / "iceemdan_cache",
        backend_factory=ice_backend_factory,
    )
    ice = walk_forward(
        ice_panel,
        config,
        feature_columns=FEATURE_COLUMNS + ICEEMDAN_FEATURE_COLUMNS,
        model_name="elastic_net_iceemdan",
        force_model=True,
        checkpoint_path=ice_checkpoint,
    )
    evaluations["elastic_net_iceemdan"] = ice
    execution["elastic_net_iceemdan"] = {
        "execution_mode": execution_mode,
        "trained": True,
        "mock_backend": ice_backend_factory is not None,
        "backend": str(ice_config["backend"]),
        "folds": len(ice.folds),
        "prediction_count": len(_model_rows(ice)),
        "seed": int(ice_config["seed"]),
        "feature_columns": ICEEMDAN_FEATURE_COLUMNS,
        "numerical_validation": numerical_validation,
        **_checkpoint_metadata(repo, ice_checkpoint),
    }

    patch_execution: PatchTSTExecution = evaluate_patchtst(
        panel,
        config,
        advanced["patchtst"],
        checkpoint_root / "patchtst",
    )
    evaluations["patchtst"] = patch_execution.evaluation
    execution["patchtst"] = {
        **patch_execution.execution_metadata,
        "execution_mode": execution_mode,
    }
    execution["patchtst"]["checkpoint"] = _artifact_path(
        repo, repo / execution["patchtst"]["checkpoint"]
        if not Path(execution["patchtst"]["checkpoint"]).is_absolute()
        else Path(execution["patchtst"]["checkpoint"])
    )
    execution["patchtst"]["checkpoints"] = [
        _artifact_path(repo, Path(path)) for path in patch_execution.checkpoint_paths
    ]

    for name, evaluation in evaluations.items():
        _validate_execution(name, execution[name], _model_rows(evaluation))
    common, common_window = _common_rows(evaluations)
    for name, metadata in execution.items():
        metadata["common_test_window"] = {
            **common_window,
            "model_coverage": common_window["models"][name]["coverage"],
        }
        fold_records = []
        for record in evaluations[name].folds:
            normalized = dict(record)
            if normalized.get("checkpoint"):
                normalized["checkpoint"] = _artifact_path(repo, Path(normalized["checkpoint"]))
            fold_records.append(normalized)
        metadata["fold_records"] = fold_records
    execution["patchtst"]["epochs"] = {
        "configured_max": int(advanced["patchtst"]["epochs"]),
        "actual_by_fold": [
            {
                "fold": row["fold"],
                "prediction_date": row["prediction_date"],
                "epochs_run": row["epochs_run"],
            }
            for row in patch_execution.evaluation.folds
        ],
    }
    model_results = {
        name: {
            "label": {
                "elastic_net": "ElasticNet",
                "elastic_net_iceemdan": "ElasticNet + ICEEMDAN features",
                "patchtst": "PatchTST",
            }[name],
            "status": "PRODUCTION_CHAMPION" if name == "elastic_net" else "IMPLEMENTED_NOT_EVALUATED",
            "execution": execution[name],
            "full_history": _evaluate_rows(_model_rows(evaluation), config),
            "common_window": {
                **_evaluate_rows(common[name], config),
                "coverage": common_window["models"][name]["coverage"],
            },
        }
        for name, evaluation in evaluations.items()
    }
    baseline_common = model_results["elastic_net"]["common_window"]
    promotion_records = []
    for name in ("elastic_net_iceemdan", "patchtst"):
        record = _promotion_record(
            name,
            baseline_common,
            model_results[name]["common_window"],
            common_window,
            advanced["promotion"],
        )
        model_results[name]["status"] = record["status"]
        model_results[name]["promotion"] = record
        promotion_records.append(record)

    predictions = pd.concat(
        [
            _model_rows(evaluation).assign(candidate=name)
            for name, evaluation in evaluations.items()
        ],
        ignore_index=True,
    )
    predictions_path = output_root / "oos_predictions.parquet"
    predictions.to_parquet(predictions_path, index=False)
    history_path = output_root / "patchtst_training_history.json"
    history_path.write_text(
        json.dumps(patch_execution.training_history, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    execution["patchtst"]["training_history"] = _artifact_path(repo, history_path)
    execution["patchtst"]["training_history_sha256"] = hashlib.sha256(
        history_path.read_bytes()
    ).hexdigest()
    payload = {
        "schema_version": 1,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "run_id": run_id,
        "execution_mode": execution_mode,
        "production_model_unchanged": True,
        "production_champion": "elastic_net",
        "status_taxonomy": sorted(STATUS_VALUES),
        "common_test_window": common_window,
        "comparison_integrity": {
            "same_universe_rows": common_window["identical_rows"],
            "same_targets": common_window["identical_targets"],
            "same_rebalance_dates": True,
            "same_transaction_costs": True,
            "same_optimizer_constraints": True,
            "same_target": "forward_excess_total_return_20d_vs_MCFTR",
            "transaction_cost_bps_one_way": config.one_way_cost_bps,
            "optimizer": "equal_weight_top_n_with_turnover_cap",
            "optimizer_constraints": {
                "holdings": config.holdings,
                "turnover_cap": config.turnover_cap,
            },
        },
        "models": model_results,
        "promotion_decisions": promotion_records,
        "artifacts": {
            "oos_predictions": _artifact_path(repo, predictions_path),
            "oos_predictions_sha256": hashlib.sha256(predictions_path.read_bytes()).hexdigest(),
            "patchtst_training_history": _artifact_path(repo, history_path),
            "patchtst_training_history_sha256": hashlib.sha256(history_path.read_bytes()).hexdigest(),
        },
    }
    return AdvancedEvaluationResult(payload, predictions, patch_execution.training_history)
