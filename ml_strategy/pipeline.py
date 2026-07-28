from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .data import MarketData, data_age_days, load_market_data
from .features import FEATURE_COLUMNS, build_feature_panel, eligible_cross_section
from .ledger import prepare_ledger
from .models import ModelEvaluation, walk_forward
from .optimization import build_portfolio
from .schemas import publish_bundle
from .sector_features import build_sector_features, evaluate_sector_ablation
from .sector_features.registry import load_config


def _round(value, digits: int = 6):
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _previous_positions(latest_path: Path, data_cutoff: str | None = None) -> dict[str, dict]:
    if not latest_path.exists():
        return {}
    try:
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
        positions = payload.get("portfolio", {}).get("positions", [])
        same_cutoff = bool(data_cutoff and payload.get("data_as_of") == data_cutoff)
        return {
            str(row["ticker"]): {
                **row,
                "target_weight": (
                    row.get("current_weight", row.get("target_weight", 0))
                    if same_cutoff
                    else row.get("target_weight", 0)
                ),
                "shares": (
                    row.get("current_shares", row.get("shares", 0))
                    if same_cutoff
                    else row.get("shares", 0)
                ),
            }
            for row in positions
            if isinstance(row, dict) and row.get("ticker")
        }
    except (OSError, ValueError, TypeError):
        return {}


def _previous_weights(
    latest_path: Path,
    data_cutoff: str | None = None,
) -> pd.Series:
    positions = _previous_positions(latest_path, data_cutoff)
    return pd.Series(
        {
            ticker: float(row.get("target_weight", 0))
            for ticker, row in positions.items()
        },
        dtype=float,
    )


def _data_quality(data: MarketData, panel: pd.DataFrame, config: StrategyConfig, today: date) -> dict:
    as_of = data.as_of
    latest = eligible_cross_section(panel, as_of, config)
    benchmark_rows = int(data.benchmark.loc[:as_of].notna().sum())
    price_series = len(data.source_rows)
    sufficient_series = sum(rows >= config.min_history for rows in data.source_rows.values())
    dividend_series = sum(bool(data.dividends.get(ticker)) for ticker in data.close.columns)
    extreme = data.close.pct_change(fill_method=None).abs().gt(config.max_abs_daily_return)
    extreme_count = int(extreme.sum().sum())
    age = data_age_days(as_of, today)
    checks = [
        {
            "name": "price_series",
            "status": "PASS" if price_series >= config.min_cross_section else "BLOCKED",
            "value": price_series,
            "minimum": config.min_cross_section,
            "source": "MOEX ISS",
        },
        {
            "name": "history_depth",
            "status": "PASS" if sufficient_series >= config.min_cross_section else "BLOCKED",
            "value": sufficient_series,
            "minimum": config.min_cross_section,
        },
        {
            "name": "benchmark_history",
            "status": "PASS" if benchmark_rows >= config.min_history else "BLOCKED",
            "value": benchmark_rows,
            "minimum": config.min_history,
            "source": "MOEX ISS / MCFTR",
        },
        {
            "name": "latest_investable_cross_section",
            "status": "PASS" if len(latest) >= config.min_cross_section else "BLOCKED",
            "value": int(len(latest)),
            "minimum": config.min_cross_section,
        },
        {
            "name": "staleness",
            "status": "PASS" if age <= config.stale_calendar_days else "DEGRADED",
            "value_calendar_days": age,
            "maximum": config.stale_calendar_days,
        },
        {
            "name": "official_dividend_coverage",
            "status": "PASS" if dividend_series else "DEGRADED",
            "series_with_records": dividend_series,
            "universe_series": price_series,
            "source": "MOEX ISS",
        },
        {
            "name": "macro_market_features",
            "status": "PASS" if {"IMOEX", "RGBI", "USDRUB", "KEY_RATE"} <= set(data.macro) else "DEGRADED",
            "available": sorted(data.macro),
            "required": ["IMOEX", "RGBI", "USDRUB", "KEY_RATE"],
        },
        {
            "name": "unresolved_extreme_moves",
            "status": "PASS" if extreme_count == 0 else "DEGRADED",
            "excluded_observations": extreme_count,
            "threshold": config.max_abs_daily_return,
        },
        {
            "name": "historical_membership",
            "status": "DEGRADED",
            "reason": (
                "Universe membership is reconstructed from actual trading availability, but the security "
                "master is not yet a complete historical listing/delisting database."
            ),
        },
    ]
    statuses = {row["status"] for row in checks}
    status = "BLOCKED" if "BLOCKED" in statuses else ("DEGRADED" if "DEGRADED" in statuses else "PASS")
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_as_of": as_of.date().isoformat(),
        "status": status,
        "production_data": "real_sources_only",
        "checks": checks,
        "sources": [
            {"name": "MOEX ISS equity candles", "url": "https://iss.moex.com/", "as_of": as_of.date().isoformat()},
            {"name": "MOEX ISS MCFTR", "url": "https://iss.moex.com/", "as_of": data.benchmark.dropna().index.max().date().isoformat()},
            {"name": "MOEX ISS dividends", "url": "https://iss.moex.com/", "cached_records": dividend_series},
        ],
    }


def _portfolio_backtest(evaluation: ModelEvaluation, config: StrategyConfig) -> tuple[list[dict], dict]:
    rows = evaluation.predictions[evaluation.predictions["model"] == evaluation.champion].copy()
    curve: list[dict] = []
    previous = pd.Series(dtype=float)
    portfolio_returns: list[float] = []
    benchmark_returns: list[float] = []
    turnovers: list[float] = []
    nav, benchmark_nav = 1.0, 1.0
    for prediction_date, group in rows.groupby("date", sort=True):
        selected = group.nlargest(config.holdings, "forecast")
        if len(selected) < 2:
            continue
        target = pd.Series(1 / len(selected), index=selected["ticker"].astype(str))
        union = target.index.union(previous.index)
        target = target.reindex(union).fillna(0)
        previous = previous.reindex(union).fillna(0)
        delta = target - previous
        unconstrained_turnover = float(delta.abs().sum())
        scale = min(1.0, config.turnover_cap / unconstrained_turnover) if unconstrained_turnover else 1.0
        weights = previous + delta * scale
        turnover = float((weights - previous).abs().sum())
        realized = group.set_index("ticker")["forward_total_return"].reindex(weights.index).fillna(0)
        gross = float((weights * realized).sum())
        benchmark_return = float((group["forward_total_return"] - group["actual"]).median())
        net = gross - turnover * config.one_way_cost_bps / 10_000
        nav *= 1 + net
        benchmark_nav *= 1 + benchmark_return
        portfolio_returns.append(net)
        benchmark_returns.append(benchmark_return)
        turnovers.append(turnover)
        curve.append(
            {
                "date": pd.Timestamp(prediction_date).date().isoformat(),
                "portfolio_nav": _round(nav, 6),
                "benchmark_nav": _round(benchmark_nav, 6),
                "net_return": _round(net, 6),
                "benchmark_return": _round(benchmark_return, 6),
                "turnover": _round(turnover, 4),
            }
        )
        previous = weights
    if not portfolio_returns:
        raise ValueError("portfolio backtest has no periods")
    returns = np.asarray(portfolio_returns)
    benchmark = np.asarray(benchmark_returns)
    periods_per_year = 252 / config.horizon
    cumulative = np.cumprod(1 + returns)
    drawdown = cumulative / np.maximum.accumulate(cumulative) - 1
    downside = returns[returns < 0]
    years = len(returns) / periods_per_year
    cagr = cumulative[-1] ** (1 / years) - 1 if years > 0 and cumulative[-1] > 0 else None
    volatility = returns.std(ddof=1) * np.sqrt(periods_per_year) if len(returns) > 1 else None
    sharpe = returns.mean() / returns.std(ddof=1) * np.sqrt(periods_per_year) if len(returns) > 1 and returns.std(ddof=1) > 0 else None
    sortino = (
        returns.mean() / downside.std(ddof=1) * np.sqrt(periods_per_year)
        if len(downside) > 1 and downside.std(ddof=1) > 0
        else None
    )
    benchmark_cumulative = float(np.prod(1 + benchmark))
    metrics = {
        "periods": len(returns),
        "cagr_after_costs": _round(cagr),
        "annualized_volatility": _round(volatility),
        "sharpe_after_costs": _round(sharpe),
        "sortino_after_costs": _round(sortino),
        "max_drawdown": _round(drawdown.min()),
        "cumulative_return_after_costs": _round(cumulative[-1] - 1),
        "benchmark_cumulative_return": _round(benchmark_cumulative - 1),
        "excess_cumulative_return": _round(cumulative[-1] - benchmark_cumulative),
        "average_turnover": _round(np.mean(turnovers)),
        "positive_period_ratio": _round(np.mean(returns > 0)),
        "cost_assumption_bps_one_way": config.one_way_cost_bps,
        "turnover_cap": config.turnover_cap,
    }
    return curve, metrics


def _portfolio_diagnostics(positions: list[dict], cash_weight: float, config: StrategyConfig) -> dict:
    weights = sorted(
        (float(row.get("target_weight") or 0) for row in positions),
        reverse=True,
    )
    invested_weight = float(sum(weights))
    normalized = [weight / invested_weight for weight in weights] if invested_weight > 0 else []
    sector_weights: dict[str, float] = {}
    for row in positions:
        sector = str(row.get("sector") or "Не определён")
        sector_weights[sector] = sector_weights.get(sector, 0.0) + float(
            row.get("target_weight") or 0
        )
    largest_sector, largest_sector_weight = (
        max(sector_weights.items(), key=lambda item: item[1])
        if sector_weights
        else (None, 0.0)
    )
    top5_weight = float(sum(weights[:5]))
    squared_weight_sum = sum(weight**2 for weight in normalized)
    effective_positions = 1.0 / squared_weight_sum if squared_weight_sum > 0 else None
    warnings: list[str] = []
    if top5_weight >= config.top5_cap * 0.90:
        warnings.append("TOP5_NEAR_LIMIT")
    if largest_sector_weight >= config.sector_cap * 0.85:
        warnings.append("SECTOR_NEAR_LIMIT")
    return {
        "positions_count": len(weights),
        "invested_weight": _round(invested_weight),
        "top5_weight": _round(top5_weight),
        "top5_limit": config.top5_cap,
        "largest_position_weight": _round(weights[0] if weights else 0),
        "position_limit": config.max_weight,
        "largest_sector": largest_sector,
        "largest_sector_weight": _round(largest_sector_weight),
        "sector_limit": config.sector_cap,
        "effective_positions": _round(effective_positions, 2),
        "warnings": warnings,
        "cash": {
            "weight": _round(cash_weight),
            "meaning": "residual_after_constraints_and_lot_rounding",
            "is_market_timing_signal": False,
        },
    }


def _execution_policy(
    production_status: str,
    data_quality_status: str,
    portfolio_metrics: dict,
) -> dict:
    cagr = portfolio_metrics.get("cagr_after_costs")
    sharpe = portfolio_metrics.get("sharpe_after_costs")
    absolute_gates = {
        "positive_cagr_after_costs": bool(cagr is not None and cagr > 0),
        "sharpe_after_costs_at_least_0_50": bool(sharpe is not None and sharpe >= 0.50),
        "production_model_approved": production_status == "APPROVED",
        "data_not_blocked": data_quality_status != "BLOCKED",
    }
    absolute_quality_pass = all(absolute_gates.values())
    model_portfolio_ready = (
        production_status == "APPROVED" and data_quality_status != "BLOCKED"
    )
    if data_quality_status == "BLOCKED":
        status = "BLOCKED"
        reason = "Критические проверки данных не пройдены."
    elif model_portfolio_ready:
        status = "MODEL_PORTFOLIO_READY"
        reason = (
            "Целевые веса и ребаланс модельного портфеля рассчитаны по последним "
            "официальным данным. Абсолютные результаты backtest остаются слабыми, "
            "поэтому автоматическое исполнение отключено."
        )
    else:
        status = "RESEARCH_ONLY"
        reason = (
            "Модель не прошла production-gate; целевые веса доступны только для исследования."
        )
    return {
        "status": status,
        "model_portfolio_ready": model_portfolio_ready,
        "manual_rebalance_plan_available": model_portfolio_ready,
        "auto_execution_allowed": False,
        "uses_user_holdings": False,
        "comparison_basis": "previous_published_model_snapshot",
        "absolute_quality_pass": absolute_quality_pass,
        "absolute_quality_gates": absolute_gates,
        "evidence": {
            "cagr_after_costs": _round(cagr),
            "sharpe_after_costs": _round(sharpe),
        },
        "reason": reason,
    }


def build_bundle(
    repo: Path,
    config: StrategyConfig | None = None,
    include_tree_challengers: bool = False,
    today: date | None = None,
) -> dict[str, dict]:
    config = config or StrategyConfig()
    today = today or date.today()
    daily_root = repo / "data" / "daily"
    data = load_market_data(
        daily_root=daily_root,
        master_path=repo / "data" / "security_master.json",
        benchmark_path=daily_root / "benchmarks" / "MCFTR.parquet",
        dividends_path=daily_root / "dividends.json",
        macro_paths={
            "IMOEX": daily_root / "benchmarks" / "IMOEX.parquet",
            "RGBI": daily_root / "benchmarks" / "RGBI.parquet",
            "USDRUB": daily_root / "benchmarks" / "USDRUB.parquet",
            "KEY_RATE": daily_root / "benchmarks" / "KEY_RATE.parquet",
        },
    )
    panel = build_feature_panel(data, config)
    quality = _data_quality(data, panel, config, today)
    if quality["status"] == "BLOCKED":
        reasons = [row["name"] for row in quality["checks"] if row["status"] == "BLOCKED"]
        raise ValueError("data quality blocked: " + ", ".join(reasons))
    sector_result = build_sector_features(data, panel, repo)
    feature_flags = load_config(repo / "config" / "ml_strategy" / "sector_feature_flags.yml")
    ablation_rows, approved_columns, ablation_evaluations = evaluate_sector_ablation(
        sector_result,
        config,
        feature_flags["promotion"],
    )
    base_portfolio_metrics = _portfolio_backtest(ablation_evaluations["BASE"], config)[1]
    for row in ablation_rows:
        candidate = ablation_evaluations[f"{row['pack_id']}:SECTOR_FEATURES"]
        candidate_metrics = _portfolio_backtest(candidate, config)[1]
        row["base_after_costs"] = base_portfolio_metrics
        row["candidate_after_costs"] = candidate_metrics
        after_costs_pass = (
            (candidate_metrics.get("excess_cumulative_return") or -1.0)
            > (base_portfolio_metrics.get("excess_cumulative_return") or -1.0)
            and (candidate_metrics.get("sharpe_after_costs") or -1.0)
            >= (base_portfolio_metrics.get("sharpe_after_costs") or -1.0)
        )
        row["after_costs_gate"] = "PASS" if after_costs_pass else "FAIL"
        if row["status"] == "APPROVED" and not after_costs_pass:
            row["status"] = "RESEARCH_ONLY"
            row["reason"] = "Forecast gate passed, but the fixed after-cost portfolio gate failed."
            pack_columns = sector_result.pack_columns[row["pack_id"]]
            sector_column = f"sector_id__{row['pack_id'].lower()}"
            approved_columns = [
                column for column in approved_columns if column not in {sector_column, *pack_columns}
            ]
    ablation_by_pack = {row["pack_id"]: row for row in ablation_rows}
    for row in sector_result.pack_rows:
        result = ablation_by_pack[row["pack_id"]]
        row["ablation_status"] = result["status"]
        row["status"] = result["status"]
        row["ablation_reason"] = result["reason"]
    sector_result.quality_payload["packs"] = sector_result.pack_rows
    sector_result.quality_payload["approved_feature_columns"] = approved_columns
    sector_result.quality_payload["ablation"] = ablation_rows
    feature_columns = FEATURE_COLUMNS + approved_columns
    evaluation = walk_forward(
        sector_result.panel,
        config,
        include_tree_challengers=include_tree_challengers,
        feature_columns=feature_columns,
    )
    as_of = data.as_of
    panel = sector_result.panel
    latest = eligible_cross_section(panel, as_of, config)
    latest = latest.reindex(evaluation.latest_forecasts.index)
    latest["lot_size"] = [
        int(data.master.get(ticker, {}).get("lot_size") or 1) for ticker in latest.index
    ]
    previous_path = repo / "data" / "ml_strategy" / "latest.json"
    data_cutoff = as_of.date().isoformat()
    previous_positions = _previous_positions(previous_path, data_cutoff)
    previous = _previous_weights(previous_path, data_cutoff)
    price_returns = data.close.reindex(data.benchmark.index.intersection(data.close.index)).pct_change(
        fill_method=None
    )
    portfolio = build_portfolio(
        forecasts=evaluation.latest_forecasts,
        returns=price_returns.loc[:as_of],
        metadata=latest,
        previous_weights=previous,
        config=config,
        prefer_max_sharpe=evaluation.champion_status == "APPROVED",
    )
    positions: list[dict] = []
    all_names = portfolio.executable_weights.index
    for ticker in all_names:
        current = float(previous.get(ticker, 0))
        target = float(portfolio.executable_weights.get(ticker, 0))
        theoretical = float(portfolio.theoretical_weights.get(ticker, 0))
        price = float(latest.at[ticker, "close"])
        target_shares = int(portfolio.shares.get(ticker, 0))
        current_shares = int(previous_positions.get(ticker, {}).get("shares") or 0)
        trade_shares = target_shares - current_shares
        position = {
                "ticker": ticker,
                "name": data.master.get(ticker, {}).get("name") or ticker,
                "sector": data.master.get(ticker, {}).get("sector") or "Не определён",
                "current_weight": _round(current),
                "theoretical_weight": _round(theoretical),
                "target_weight": _round(target),
                "change_weight": _round(target - current),
                "shares": target_shares,
                "current_shares": current_shares,
                "trade_shares": trade_shares,
                "trade_value_rub": _round(abs(trade_shares) * price, 2),
                "trade_action": "BUY" if trade_shares > 0 else ("SELL" if trade_shares < 0 else "HOLD"),
                "price_rub": _round(price, 4),
                "expected_excess_return_20d": _round(evaluation.latest_forecasts.get(ticker)),
                "adv_20d_rub": _round(latest.at[ticker, "adv_20d"], 2),
                "beta_120d": _round(latest.at[ticker, "beta_120d"], 3),
                "sector_feature_pack": None,
                "sector_drivers": [],
            }
        pack_row = next(
            (
                row
                for row in sector_result.pack_rows
                if latest.at[ticker, f"sector_id__{row['pack_id'].lower()}"] == 1
            ),
            None,
        )
        driver_labels = {
            "oil_fx_driver": "USD/RUB, 20 дней",
            "steel_fx_driver": "USD/RUB, 20 дней",
            "bank_key_rate_level": "Ключевая ставка",
            "bank_key_rate_change_60d": "Изменение ставки, 60 дней",
            "bank_rgbi_driver": "RGBI, 20 дней",
            "developer_key_rate_level": "Ключевая ставка",
            "developer_key_rate_change_60d": "Изменение ставки, 60 дней",
            "developer_rgbi_driver": "RGBI, 20 дней",
        }
        if pack_row:
            pack_id = pack_row["pack_id"]
            position["sector_feature_pack"] = {
                "pack_id": pack_id,
                "label": pack_row["label"],
                "status": pack_row["status"],
                "included_in_model": pack_row["status"] == "APPROVED",
                "blocked_sources": pack_row.get("blocked_sources", []),
            }
            for column in sector_result.pack_columns[pack_id]:
                if column.endswith("_missing") or pd.isna(latest.at[ticker, column]):
                    continue
                value = float(latest.at[ticker, column])
                position["sector_drivers"].append(
                    {
                        "factor": driver_labels.get(column, column),
                        "value": _round(value, 4),
                        "direction": "positive" if value > 0 else ("negative" if value < 0 else "neutral"),
                        "data_as_of": as_of.date().isoformat(),
                        "status": pack_row["status"],
                    }
                )
                if len(position["sector_drivers"]) == 3:
                    break
        positions.append(position)
    positions.sort(key=lambda row: row["target_weight"], reverse=True)
    curve, portfolio_metrics = _portfolio_backtest(evaluation, config)
    production_status = evaluation.champion_status
    if production_status == "APPROVED" and (
        (portfolio_metrics.get("excess_cumulative_return") or 0) <= 0
        or (portfolio_metrics.get("sharpe_after_costs") or 0) <= 0
    ):
        production_status = "RESEARCH_ONLY"
    if data_age_days(as_of, today) > config.stale_calendar_days:
        action, reason = "DATA_STALE", "Последние официальные рыночные данные старше допустимого порога."
    elif production_status != "APPROVED":
        action, reason = "MODEL_UNCERTAIN", "Модель не прошла одновременно прогнозный и портфельный gate после издержек."
    elif previous.empty:
        action, reason = "WATCH", "Первый live snapshot: сначала накопить наблюдения, затем оценивать ребалансировку."
    elif portfolio.turnover >= 0.08:
        action, reason = "REBALANCE", "Исполнимые целевые веса заметно отличаются от предыдущего model snapshot."
    else:
        action, reason = "NO_ACTION", "Изменение остаётся внутри no-trade zone."
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    diagnostics = _portfolio_diagnostics(positions, portfolio.cash_weight, config)
    execution_policy = _execution_policy(
        production_status,
        quality["status"],
        portfolio_metrics,
    )
    latest_payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "data_as_of": as_of.date().isoformat(),
        "benchmark": "MCFTR",
        "horizon_sessions": config.horizon,
        "signal": {"action": action, "reason": reason, "mode": "20_session_rebalance_threshold"},
        "portfolio": {
            "method": portfolio.method,
            "capital_rub": config.capital_rub,
            "positions": positions,
            "cash_weight": _round(portfolio.cash_weight),
            "turnover": _round(portfolio.turnover),
            "estimated_cost_rub": _round(portfolio.estimated_cost_rub, 2),
            "one_way_cost_bps": config.one_way_cost_bps,
            "annualized_volatility": _round(portfolio.annualized_volatility),
            "beta": _round(portfolio.beta),
            "fallback_reason": portfolio.fallback_reason,
            "diagnostics": diagnostics,
        },
        "model": {
            "champion": evaluation.champion,
            "status": production_status,
            "prediction_gate_status": evaluation.champion_status,
            "forecast_shrinkage": config.forecast_shrinkage,
            "update_policy": {
                "daily_process": "locked_spec_expanding_window_refit",
                "specification_locked": True,
                "hyperparameters_locked": True,
                "selection_scope": "elastic_net_vs_naive_baselines",
                "challenger_promotion": "separate_scheduled_evaluation_only",
            },
        },
        "execution_policy": execution_policy,
        "data_quality": {
            "status": quality["status"],
            "investable_companies": int(len(latest)),
            "stale_days": data_age_days(as_of, today),
        },
        "sector_features": {
            "status": sector_result.quality_payload["status"],
            "approved_packs": [
                row["pack_id"] for row in sector_result.pack_rows if row["status"] == "APPROVED"
            ],
            "research_only_packs": [
                row["pack_id"] for row in sector_result.pack_rows if row["status"] == "RESEARCH_ONLY"
            ],
            "blocked_issuer_exposures": True,
            "last_checked_at": sector_result.quality_payload["generated_at"],
        },
        "limitations": [
            "Исследовательский модельный портфель, не индивидуальная инвестиционная рекомендация.",
            "Изменения сравниваются с предыдущим модельным snapshot, а не с портфелем пользователя.",
            "Cash — технический остаток после ограничений и округления до лотов, а не прогноз падения рынка.",
            "Исторический состав бумаг пока неполон: в backtest остаётся остаточный survivorship risk.",
            "Неразрешённые split-like наблюдения исключаются, а не исправляются догадкой.",
            "Прогнозы неопределённы; система может воздержаться от изменения портфеля.",
        ],
    }
    backtest = {
        "schema_version": 1,
        "generated_at": generated_at,
        "benchmark": "MCFTR",
        "target": "forward_excess_total_return_20d_vs_MCFTR",
        "validation": "purged_walk_forward",
        "folds": evaluation.folds,
        "model_metrics": evaluation.metrics,
        "portfolio_metrics": portfolio_metrics,
        "sector_ablation": ablation_rows,
        "curve": curve,
    }
    model_card = {
        "schema_version": 1,
        "generated_at": generated_at,
        "champion": {
            "name": evaluation.champion,
            "status": production_status,
            "prediction_gate_status": evaluation.champion_status,
            "portfolio_gate": "after_cost_excess_positive_and_sharpe_positive",
        },
        "challengers": evaluation.challengers
        + [
            {
                "name": "PatchTST",
                "status": "IMPLEMENTED_NOT_EVALUATED",
                "reason": (
                    "Implemented as a channel-independent patched Transformer. "
                    "Promotion is decided only by the separate scheduled production evaluation."
                ),
            },
            {
                "name": "ElasticNet + ICEEMDAN features",
                "status": "IMPLEMENTED_NOT_EVALUATED",
                "reason": (
                    "The Colominas et al. ICEEMDAN port is implemented as a feature ablation. "
                    "Promotion is decided only by the separate scheduled production evaluation."
                ),
            },
        ],
        "features": feature_columns,
        "sector_features": {
            "packs": sector_result.pack_rows,
            "approved_feature_columns": approved_columns,
            "issuer_exposures": "BLOCKED",
            "ablation_protocol": (
                "same folds, universe, model, optimizer config, costs and test dates; "
                "fixed promotion thresholds declared before evaluation"
            ),
        },
        "target": {
            "name": "forward_excess_total_return_20d_vs_MCFTR",
            "horizon_sessions": config.horizon,
            "dividends": "official MOEX ISS records when present",
            "execution_lag_sessions": config.execution_lag_sessions,
        },
        "training": {
            "validation": "purged_walk_forward",
            "purge_rule": "target_end_date < prediction_date",
            "scaler_scope": "fit inside each training fold",
            "random_seed": config.random_seed,
        },
        "portfolio": {
            "production_optimizer": portfolio.method,
            "implemented": ["HRP", "constrained_max_sharpe", "minimum_variance", "equal_weight_fallback"],
            "constraints": config.to_dict(),
        },
        "limitations": latest_payload["limitations"],
    }
    return {
        "latest.json": latest_payload,
        "backtest.json": backtest,
        "model_card.json": model_card,
        "data_quality.json": quality,
        "sector_features/latest_registry.json": sector_result.registry_payload,
        "sector_features/latest_quality.json": sector_result.quality_payload,
    }


def run_pipeline(
    repo: Path,
    config: StrategyConfig | None = None,
    include_tree_challengers: bool = False,
    today: date | None = None,
) -> dict[str, dict]:
    effective_config = config or StrategyConfig()
    bundle = build_bundle(
        repo,
        config=effective_config,
        include_tree_challengers=include_tree_challengers,
        today=today,
    )
    ledger_files, live_track_record = prepare_ledger(
        repo,
        bundle["latest.json"],
        bundle["model_card.json"],
        effective_config,
    )
    bundle["latest.json"]["live_track_record"] = live_track_record
    bundle.update(ledger_files)
    history_date = bundle["latest.json"]["data_as_of"]
    publish_bundle(
        bundle,
        data_root=repo / "data" / "ml_strategy",
        site_root=repo / "site" / "ml_strategy",
        history_date=history_date,
    )
    return bundle
