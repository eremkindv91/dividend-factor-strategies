from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .data import MarketData, data_age_days, load_market_data
from .features import FEATURE_COLUMNS, build_feature_panel, eligible_cross_section
from .models import ModelEvaluation, walk_forward
from .optimization import build_portfolio
from .execution import (
    decide_strategy_state,
    extract_published_portfolio,
    public_candidate,
    strip_execution,
)
from .schemas import publish_bundle
from .sector_features import build_sector_features, evaluate_sector_ablation
from .sector_features.registry import load_config


def _round(value, digits: int = 6):
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _previous_snapshot(latest_path: Path) -> dict:
    if not latest_path.exists():
        return {}
    try:
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _published_weights(snapshot: dict) -> pd.Series:
    published = extract_published_portfolio(snapshot)
    positions = (published or {}).get("positions") or []
    return pd.Series(
        {row["ticker"]: float(row.get("target_weight", 0)) for row in positions if row.get("ticker")},
        dtype=float,
    )


def _stable_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


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
            "MOEXOG": daily_root / "benchmarks" / "MOEXOG.parquet",
            "MOEXMM": daily_root / "benchmarks" / "MOEXMM.parquet",
            "MOEXFN": daily_root / "benchmarks" / "MOEXFN.parquet",
            "MOEXRE": daily_root / "benchmarks" / "MOEXRE.parquet",
            "MOEXEU": daily_root / "benchmarks" / "MOEXEU.parquet",
            "MOEXCN": daily_root / "benchmarks" / "MOEXCN.parquet",
            "MOEXIT": daily_root / "benchmarks" / "MOEXIT.parquet",
            "MOEXTL": daily_root / "benchmarks" / "MOEXTL.parquet",
            "MOEXTN": daily_root / "benchmarks" / "MOEXTN.parquet",
            "MOEXCH": daily_root / "benchmarks" / "MOEXCH.parquet",
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
    pack_quality = {row["pack_id"]: row for row in sector_result.pack_rows}
    for row in ablation_rows:
        quality_row = pack_quality[row["pack_id"]]
        reference = ablation_evaluations[f"{row['pack_id']}:SECTOR_ID"]
        candidate = ablation_evaluations[f"{row['pack_id']}:SECTOR_FEATURES"]
        base_portfolio_metrics = _portfolio_backtest(reference, config)[1]
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
        is_timing = row["feature_role"] == "sector_timing"
        signal_ic = row["timing_candidate_spearman_ic"] if is_timing else row["candidate_spearman_ic"]
        signal_ic_gain = (
            row["timing_spearman_ic_improvement"]
            if is_timing else row["spearman_ic_improvement"]
        )
        signal_ic_minimum = (
            row["minimum_timing_ic_improvement"]
            if is_timing else float(feature_flags["promotion"]["minimum_spearman_ic_improvement"])
        )
        signal_hit_gain = row["timing_hit_rate_change"] if is_timing else row["hit_rate_change"]
        signal_spread = (
            row["timing_candidate_top_bottom_spread"]
            if is_timing else row["candidate_top_bottom_spread"]
        )
        row["promotion_gates"] = {
            "source_availability": {
                "status": "PASS"
                if not quality_row.get("unavailable_sources")
                else "FAIL",
                "unavailable_sources": quality_row.get("unavailable_sources", []),
            },
            "source_freshness": {
                "status": "PASS" if not quality_row.get("stale_sources") else "FAIL",
                "stale_sources": quality_row.get("stale_sources", []),
                "latest_sector_index_at": quality_row.get("latest_sector_index_at"),
                "sector_index_age_days": quality_row.get("sector_index_age_days"),
            },
            "identical_test_rows": {
                "status": "PASS" if (
                    row["base_n"] == row["candidate_n"]
                    and row["same_folds"]
                    and row["sector_same_rows"]
                ) else "FAIL",
                "base_n": row["base_n"],
                "candidate_n": row["candidate_n"],
            },
            "sector_oos_evidence": {
                "status": "PASS" if (
                    row["sector_oos_rows"] >= row["minimum_sector_oos_rows"]
                    and row["sector_oos_tickers"] >= row["minimum_sector_tickers"]
                    and (
                        row["timing_dates"] >= row["minimum_timing_dates"]
                        if is_timing else row["sector_oos_dates"] >= row["minimum_sector_dates"]
                    )
                ) else "FAIL",
                "rows": row["sector_oos_rows"],
                "tickers": row["sector_oos_tickers"],
                "dates": row["sector_oos_dates"],
                "minimum_rows": row["minimum_sector_oos_rows"],
                "minimum_tickers": row["minimum_sector_tickers"],
                "minimum_dates": row["minimum_sector_dates"],
                "timing_dates": row["timing_dates"],
                "minimum_timing_dates": row["minimum_timing_dates"],
            },
            "rank_ic_improvement": {
                "status": "PASS" if (
                    signal_ic is not None
                    and signal_ic >= (row["minimum_timing_ic"] if is_timing else -1.0)
                    and signal_ic_gain >= signal_ic_minimum
                ) else "FAIL",
                "scope": row["evaluation_scope"],
                "actual": signal_ic_gain,
                "candidate_ic": signal_ic,
                "minimum": signal_ic_minimum,
            },
            "hit_rate_change": {
                "status": "PASS" if signal_hit_gain >= float(feature_flags["promotion"]["minimum_hit_rate_change"]) else "FAIL",
                "actual": signal_hit_gain,
                "minimum": float(feature_flags["promotion"]["minimum_hit_rate_change"]),
            },
            "positive_top_bottom_spread": {
                "status": "PASS" if (signal_spread or 0) > 0 else "FAIL",
                "actual": signal_spread,
                "required": bool(feature_flags["promotion"].get("require_positive_top_bottom_spread", True)),
            },
            "relative_after_cost_portfolio": {
                "status": "PASS" if after_costs_pass else "FAIL",
                "base_excess": base_portfolio_metrics.get("excess_cumulative_return"),
                "candidate_excess": candidate_metrics.get("excess_cumulative_return"),
                "base_sharpe": base_portfolio_metrics.get("sharpe_after_costs"),
                "candidate_sharpe": candidate_metrics.get("sharpe_after_costs"),
            },
        }
        if row["status"] == "APPROVED" and not after_costs_pass:
            row["status"] = "RESEARCH_ONLY"
            row["reason"] = "Forecast gate passed, but the fixed after-cost portfolio gate failed."
            pack_columns = sector_result.pack_columns[row["pack_id"]]
            sector_column = f"sector_id__{row['pack_id'].lower()}"
            approved_columns = [
                column for column in approved_columns if column not in {sector_column, *pack_columns}
            ]
        if row["status"] == "APPROVED" and (
            quality_row.get("unavailable_sources") or quality_row.get("stale_sources")
        ):
            row["status"] = "RESEARCH_ONLY"
            row["reason"] = "Required official sector source is unavailable or stale in this run."
            pack_columns = sector_result.pack_columns[row["pack_id"]]
            sector_column = f"sector_id__{row['pack_id'].lower()}"
            approved_columns = [
                column for column in approved_columns if column not in {sector_column, *pack_columns}
            ]
        row["failed_gates"] = [
            name for name, gate in row["promotion_gates"].items() if gate["status"] == "FAIL"
        ]
        row["used_in_production"] = row["status"] == "APPROVED"
        if row["failed_gates"]:
            row["reason"] = "Не пройдены фиксированные gates: " + ", ".join(row["failed_gates"]) + "."
    ablation_by_pack = {row["pack_id"]: row for row in ablation_rows}
    for row in sector_result.pack_rows:
        result = ablation_by_pack[row["pack_id"]]
        row["ablation_status"] = result["status"]
        row["status"] = result["status"]
        row["ablation_reason"] = result["reason"]
        row["used_in_production"] = result["used_in_production"]
        row["evaluation"] = {
            "scope": result["evaluation_scope"],
            "feature_role": result["feature_role"],
            "reference_model": result["reference_model"],
            "base_n": result["base_n"],
            "candidate_n": result["candidate_n"],
            "sector_oos_rows": result["sector_oos_rows"],
            "sector_oos_tickers": result["sector_oos_tickers"],
            "sector_oos_dates": result["sector_oos_dates"],
            "base_rank_ic": result["base_spearman_ic"],
            "candidate_rank_ic": result["candidate_spearman_ic"],
            "rank_ic_improvement": result["spearman_ic_improvement"],
            "rank_ic_minimum": result["promotion_gates"]["rank_ic_improvement"]["minimum"],
            "hit_rate_change": result["hit_rate_change"],
            "top_bottom_spread": result["candidate_top_bottom_spread"],
            "global_rank_ic_change": result["global_spearman_ic_change"],
            "timing_dates": result["timing_dates"],
            "timing_base_rank_ic": result["timing_base_spearman_ic"],
            "timing_candidate_rank_ic": result["timing_candidate_spearman_ic"],
            "timing_rank_ic_improvement": result["timing_spearman_ic_improvement"],
            "timing_hit_rate_change": result["timing_hit_rate_change"],
            "timing_top_bottom_spread": result["timing_candidate_top_bottom_spread"],
            "after_costs_gate": result["after_costs_gate"],
            "candidate_excess_after_costs": result["candidate_after_costs"].get("excess_cumulative_return"),
            "candidate_sharpe_after_costs": result["candidate_after_costs"].get("sharpe_after_costs"),
            "failed_gates": result["failed_gates"],
        }
    sector_result.quality_payload["packs"] = sector_result.pack_rows
    sector_result.quality_payload["approved_feature_columns"] = approved_columns
    sector_result.quality_payload["ablation"] = ablation_rows
    sector_result.quality_payload["evaluated_pack_count"] = len(ablation_rows)
    sector_result.quality_payload["production_pack_count"] = sum(
        row["status"] == "APPROVED" for row in ablation_rows
    )
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
    previous_snapshot = _previous_snapshot(repo / "data" / "ml_strategy" / "latest.json")
    previous_published = extract_published_portfolio(previous_snapshot)
    previous = _published_weights(previous_snapshot)
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
        position = {
                "ticker": ticker,
                "name": data.master.get(ticker, {}).get("name") or ticker,
                "sector": data.master.get(ticker, {}).get("sector") or "Не определён",
                "current_weight": _round(current),
                "theoretical_weight": _round(theoretical),
                "target_weight": _round(target),
                "change_weight": _round(target - current),
                "shares": int(portfolio.shares.get(ticker, 0)),
                "price_rub": _round(latest.at[ticker, "close"], 4),
                "expected_excess_return_20d": _round(evaluation.latest_forecasts.get(ticker)),
                "adv_20d_rub": _round(latest.at[ticker, "adv_20d"], 2),
                "beta_120d": _round(latest.at[ticker, "beta_120d"], 3),
                "sector_drivers": [],
            }
        pack_id = next(
            (
                row["pack_id"]
                for row in sector_result.pack_rows
                if row["status"] == "APPROVED"
                and latest.at[ticker, f"sector_id__{row['pack_id'].lower()}"] == 1
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
        if pack_id:
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
                        "status": "APPROVED",
                    }
                )
                if len(position["sector_drivers"]) == 3:
                    break
        positions.append(position)
    positions.sort(key=lambda row: row["target_weight"], reverse=True)
    curve, portfolio_metrics = _portfolio_backtest(evaluation, config)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prediction_gate_passed = evaluation.champion_status == "APPROVED"
    portfolio_gate_passed = (
        (portfolio_metrics.get("excess_cumulative_return") or 0) > 0
        and (portfolio_metrics.get("sharpe_after_costs") or 0) > 0
    )
    data_status = "stale" if data_age_days(as_of, today) > config.stale_calendar_days else quality["status"].lower()
    model_status = "production" if prediction_gate_passed and portfolio_gate_passed else "research_only"
    decision = decide_strategy_state(
        model_status=model_status,
        data_status=data_status,
        predictive_gate_passed=prediction_gate_passed,
        portfolio_gate_passed=portfolio_gate_passed,
        solver_succeeded=True,
        has_published_portfolio=bool(previous_published),
        material_change=portfolio.turnover >= 0.08,
    )
    artifact_hash = _stable_hash({"model": evaluation.champion, "features": feature_columns, "metrics": evaluation.metrics})
    constraints_hash = _stable_hash(config.to_dict())
    run_id = f"{as_of.date().isoformat()}-{artifact_hash[:8]}-{constraints_hash[:8]}"
    common_metadata = {
        "run_id": run_id,
        "as_of": as_of.date().isoformat(),
        "calculated_at": generated_at,
        "model_version": f"{evaluation.champion}:{artifact_hash}",
        "artifact_hash": artifact_hash,
        "universe_version": _stable_hash(sorted(latest.index.astype(str))),
        "features_version": _stable_hash(feature_columns),
        "constraints_hash": constraints_hash,
        "cost_model_version": f"one_way_{config.one_way_cost_bps:g}bps_v1",
        "signal_valid_until": None,
        "next_review_at": None,
        "next_execution_at": None,
    }
    candidate_portfolio = {
        **common_metadata,
        "status": (
            "accepted" if decision.publish_candidate
            else "accepted_no_change" if decision.signal_status == "valid"
            else "rejected"
        ),
        "method": portfolio.method,
        "positions": [public_candidate(row) for row in positions],
        "cash_weight": _round(portfolio.cash_weight),
        "diagnostic_turnover": _round(portfolio.turnover),
        "diagnostic_cost_rub": _round(portfolio.estimated_cost_rub, 2),
        "affects_current_portfolio": False,
    }
    executable_portfolio = {
        **common_metadata,
        "published_from_run_id": run_id,
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
    }
    if decision.publish_candidate:
        published_portfolio = executable_portfolio
    elif previous_published:
        published_portfolio = previous_published
    else:
        published_portfolio = None
    public_portfolio = published_portfolio or strip_execution(executable_portfolio)
    execution = {
        "status": decision.action_status,
        "turnover": executable_portfolio["turnover"] if decision.publish_candidate else None,
        "estimated_cost_rub": executable_portfolio["estimated_cost_rub"] if decision.publish_candidate else None,
        "one_way_cost_bps": config.one_way_cost_bps,
        "turnover_cap": config.turnover_cap,
        "turnover_formula": "0.5 * sum(abs(target_weight - current_weight)), including cash",
        "next_review_at": common_metadata["next_review_at"],
        "next_execution_at": common_metadata["next_execution_at"],
    }
    latest_payload = {
        "schema_version": 2,
        "generated_at": generated_at,
        "data_as_of": as_of.date().isoformat(),
        "benchmark": "MCFTR",
        "horizon_sessions": config.horizon,
        "run": common_metadata,
        "model_status": decision.model_status,
        "data_status": decision.data_status,
        "signal_status": decision.signal_status,
        "action_status": decision.action_status,
        "signal": {"action": decision.action_status, "status": decision.signal_status, "title": decision.title, "reason": decision.reason, "mode": "monthly_threshold"},
        "published_portfolio": published_portfolio,
        "candidate_portfolio": candidate_portfolio,
        "execution": execution,
        "portfolio": public_portfolio,
        "model": {
            "champion": evaluation.champion,
            "status": decision.model_status,
            "prediction_gate_status": evaluation.champion_status,
            "forecast_shrinkage": config.forecast_shrinkage,
        },
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
        "diagnostics": {
            "predictive_gate": {
                "status": "pass" if prediction_gate_passed else "fail",
                "actual": evaluation.metrics.get(evaluation.champion, {}),
                "thresholds": {"spearman_ic_above_best_baseline_by": 0.01, "hit_rate_minimum": 0.50},
            },
            "portfolio_gate": {
                "status": "pass" if portfolio_gate_passed else "fail",
                "actual": {
                    "excess_cumulative_return": portfolio_metrics.get("excess_cumulative_return"),
                    "sharpe_after_costs": portfolio_metrics.get("sharpe_after_costs"),
                },
                "thresholds": {"excess_cumulative_return": "> 0", "sharpe_after_costs": "> 0"},
            },
            "constraints": {
                "turnover_actual": _round(portfolio.turnover),
                "turnover_cap": config.turnover_cap,
                "one_way_cost_bps": config.one_way_cost_bps,
                "solver_method": portfolio.method,
            },
        },
        "limitations": [
            "Исследовательский модельный портфель, не индивидуальная инвестиционная рекомендация.",
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
            "status": decision.model_status,
            "prediction_gate_status": evaluation.champion_status,
            "portfolio_gate": "after_cost_excess_positive_and_sharpe_positive",
        },
        "challengers": evaluation.challengers
        + [
            {
                "name": "PatchTST",
                "status": "BLOCKED",
                "reason": "Not activated: verified panel length and sequence infrastructure are insufficient for production use.",
            },
            {
                "name": "ICEEMDAN",
                "status": "BLOCKED",
                "reason": (
                    "Expanding-only leakage-safe adapter exists, but no audited ICEEMDAN backend or "
                    "stable out-of-sample ablation is available. CEEMDAN is not relabelled as ICEEMDAN."
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
    bundle = build_bundle(repo, config=config, include_tree_challengers=include_tree_challengers, today=today)
    history_date = bundle["latest.json"]["data_as_of"]
    publish_bundle(
        bundle,
        data_root=repo / "data" / "ml_strategy",
        site_root=repo / "site" / "ml_strategy",
        history_date=history_date,
    )
    return bundle
