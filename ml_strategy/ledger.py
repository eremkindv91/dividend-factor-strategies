from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from .config import StrategyConfig

LEDGER_SCHEMA_VERSION = "1.0.0"
GENESIS_HASH = "0" * 64


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _record_hash(previous_hash: str, record: dict) -> str:
    body = {key: value for key, value in record.items() if key != "content_hash"}
    return hashlib.sha256((previous_hash + _canonical(body)).encode("utf-8")).hexdigest()


def empty_index() -> dict:
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "mode": "SHADOW_LIVE",
        "chain_head": GENESIS_HASH,
        "records": [],
        "resolutions": [],
        "metrics": {
            "status": "INSUFFICIENT_HISTORY",
            "resolved_forecasts": 0,
            "open_forecasts": 0,
        },
        "updated_at": None,
    }


def validate_ledger(index: dict) -> list[str]:
    errors: list[str] = []
    if index.get("schema_version") != LEDGER_SCHEMA_VERSION:
        errors.append("ledger: invalid schema_version")
    records = index.get("records")
    resolutions = index.get("resolutions")
    if not isinstance(records, list) or not isinstance(resolutions, list):
        return errors + ["ledger: records/resolutions must be arrays"]
    previous = GENESIS_HASH
    seen: set[str] = set()
    required_record_fields = {
        "forecast_id",
        "created_at",
        "data_cutoff",
        "ticker",
        "horizon_trading_days",
        "point_forecast_excess_return",
        "model_bundle_version",
        "feature_version",
        "dataset_version",
        "status",
        "previous_record_hash",
        "content_hash",
    }
    for record in records:
        forecast_id = record.get("forecast_id")
        if not forecast_id or forecast_id in seen:
            errors.append("ledger: duplicate or missing forecast_id")
            continue
        missing = sorted(required_record_fields - record.keys())
        if missing:
            errors.append(f"ledger: missing fields for {forecast_id}: {', '.join(missing)}")
        expected = _record_hash(previous, record)
        if record.get("previous_record_hash") != previous:
            errors.append(f"ledger: broken previous hash for {forecast_id}")
        if record.get("content_hash") != expected:
            errors.append(f"ledger: invalid content hash for {forecast_id}")
        previous = str(record.get("content_hash") or previous)
        seen.add(str(forecast_id))
    if index.get("chain_head") != previous:
        errors.append("ledger: chain_head mismatch")
    resolved_ids: set[str] = set()
    for resolution in resolutions:
        forecast_id = resolution.get("forecast_id")
        if forecast_id not in seen:
            errors.append(f"ledger: resolution references unknown forecast {forecast_id}")
        if forecast_id in resolved_ids:
            errors.append(f"ledger: duplicate resolution for {forecast_id}")
        expected = _hash({key: value for key, value in resolution.items() if key != "content_hash"})
        if resolution.get("content_hash") != expected:
            errors.append(f"ledger: invalid resolution hash for {forecast_id}")
        resolved_ids.add(str(forecast_id))
    return errors


def load_index(repo: Path) -> dict:
    for path in (
        repo / "data" / "ml_strategy" / "ledger" / "index.json",
        repo / "site" / "ml_strategy" / "ledger" / "index.json",
    ):
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            errors = validate_ledger(payload)
            if errors:
                raise ValueError("; ".join(errors))
            return payload
    return empty_index()


def _dataset_snapshot_hash(latest: dict) -> str:
    positions = sorted(
        [
            {
                "ticker": row.get("ticker"),
                "price_rub": row.get("price_rub"),
                "forecast": row.get("expected_excess_return_20d"),
            }
            for row in latest.get("portfolio", {}).get("positions", [])
        ],
        key=lambda row: str(row["ticker"]),
    )
    return _hash(
        {
            "data_as_of": latest.get("data_as_of"),
            "benchmark": latest.get("benchmark"),
            "positions": positions,
        }
    )


def _feature_version(model_card: dict) -> str:
    return _hash(model_card.get("features", []))[:16]


def _forecast_id(data_cutoff: str, ticker: str, model_bundle_version: str) -> str:
    return hashlib.sha256(
        f"{data_cutoff}|{ticker}|{model_bundle_version}".encode("utf-8")
    ).hexdigest()[:32]


def _forecast_rank_buckets(positions: list[dict]) -> dict[str, int]:
    ranked = sorted(
        positions,
        key=lambda row: (
            float(row.get("expected_excess_return_20d") or 0),
            str(row.get("ticker") or ""),
        ),
    )
    count = len(ranked)
    if not count:
        return {}
    return {
        str(row["ticker"]): min(10, int(index * 10 / count) + 1)
        for index, row in enumerate(ranked)
    }


def _resolution_for_record(
    record: dict,
    repo: Path,
    config: StrategyConfig,
) -> dict | None:
    benchmark_path = repo / "data" / "daily" / "benchmarks" / "MCFTR.parquet"
    price_path = repo / "data" / "daily" / "prices" / f"{record['ticker']}.parquet"
    if not benchmark_path.exists() or not price_path.exists():
        return None
    benchmark_frame = pd.read_parquet(benchmark_path)
    benchmark_frame["trade_date"] = pd.to_datetime(benchmark_frame["trade_date"], errors="coerce")
    benchmark_frame["close"] = pd.to_numeric(benchmark_frame["close"], errors="coerce")
    benchmark = (
        benchmark_frame.dropna(subset=["trade_date", "close"])
        .drop_duplicates("trade_date", keep="last")
        .set_index("trade_date")["close"]
        .sort_index()
    )
    cutoff = pd.Timestamp(record["data_cutoff"][:10])
    future_dates = benchmark.index[benchmark.index > cutoff]
    if len(future_dates) < int(record["horizon_trading_days"]):
        return None
    target_date = future_dates[int(record["horizon_trading_days"]) - 1]
    start_dates = benchmark.index[benchmark.index <= cutoff]
    if start_dates.empty:
        return None
    start_date = start_dates[-1]
    price_frame = pd.read_parquet(price_path)
    price_frame["trade_date"] = pd.to_datetime(price_frame["trade_date"], errors="coerce")
    price_frame["close"] = pd.to_numeric(price_frame["close"], errors="coerce")
    prices = (
        price_frame.dropna(subset=["trade_date", "close"])
        .drop_duplicates("trade_date", keep="last")
        .set_index("trade_date")["close"]
        .sort_index()
        .reindex(benchmark.loc[start_date:target_date].index)
    )
    if prices.empty or pd.isna(prices.iloc[0]) or pd.isna(prices.iloc[-1]):
        return None
    returns = prices.pct_change(fill_method=None)
    dividends_path = repo / "data" / "daily" / "dividends.json"
    if dividends_path.exists():
        raw = json.loads(dividends_path.read_text(encoding="utf-8"))
        dividends = raw.get("securities", raw) if isinstance(raw, dict) else {}
        for row in dividends.get(record["ticker"], []):
            event_date = pd.Timestamp(str(row.get("registryclosedate", ""))[:10])
            if event_date <= start_date or event_date > target_date:
                continue
            available_dates = returns.index[returns.index >= event_date]
            if available_dates.empty:
                continue
            date = available_dates[0]
            location = returns.index.get_loc(date)
            value = pd.to_numeric(row.get("value"), errors="coerce")
            previous_close = prices.iloc[location - 1] if isinstance(location, (int, np.integer)) and location > 0 else np.nan
            if pd.notna(value) and value > 0 and pd.notna(previous_close) and previous_close > 0:
                base = returns.at[date]
                returns.at[date] = (0.0 if pd.isna(base) else float(base)) + float(value) / float(previous_close)
    realized_total = float(np.prod(1 + returns.iloc[1:].dropna()) - 1)
    benchmark_total = float(benchmark.at[target_date] / benchmark.at[start_date] - 1)
    realized_excess = realized_total - benchmark_total
    point = float(record["point_forecast_excess_return"])
    lower, upper = record.get("lower_bound"), record.get("upper_bound")
    resolution = {
        "forecast_id": record["forecast_id"],
        "resolved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_date": target_date.date().isoformat(),
        "realized_total_return": realized_total,
        "benchmark_total_return": benchmark_total,
        "realized_excess_return": realized_excess,
        "inside_interval": (
            bool(float(lower) <= realized_excess <= float(upper))
            if isinstance(lower, (int, float)) and isinstance(upper, (int, float))
            else None
        ),
        "direction_correct": bool(np.sign(point) == np.sign(realized_excess)),
        "rank_bucket": record.get("forecast_rank_bucket"),
        "portfolio_contribution": realized_excess * float(record.get("target_weight") or 0),
        "estimated_cost": abs(float(record.get("change_weight") or 0))
        * config.one_way_cost_bps
        / 10_000,
        "resolution_method": "official_MOEX_total_return_when_dividend_records_present_vs_MCFTR",
    }
    resolution["content_hash"] = _hash(resolution)
    return resolution


def _finite_mean(values: list[float]) -> float | None:
    clean = [float(value) for value in values if isinstance(value, (int, float)) and math.isfinite(value)]
    return float(np.mean(clean)) if clean else None


def live_metrics(records: list[dict], resolutions: list[dict]) -> dict:
    by_id = {row["forecast_id"]: row for row in records}
    resolved = [row for row in resolutions if row.get("forecast_id") in by_id]
    open_count = len(records) - len(resolved)
    points = np.asarray([float(by_id[row["forecast_id"]]["point_forecast_excess_return"]) for row in resolved])
    actuals = np.asarray([float(row["realized_excess_return"]) for row in resolved])
    ic = (
        float(spearmanr(points, actuals).statistic)
        if len(resolved) >= 3 and np.ptp(points) > 0 and np.ptp(actuals) > 0
        else None
    )
    interval_rows = [row for row in resolved if isinstance(row.get("inside_interval"), bool)]
    directional = [row["direction_correct"] for row in resolved]
    interval_widths = [
        float(by_id[row["forecast_id"]]["upper_bound"])
        - float(by_id[row["forecast_id"]]["lower_bound"])
        for row in interval_rows
    ]
    top = [
        float(row["realized_excess_return"])
        for row in resolved
        if (row.get("rank_bucket") or 0) >= 9
    ]
    bottom = [
        float(row["realized_excess_return"])
        for row in resolved
        if 0 < (row.get("rank_bucket") or 0) <= 2
    ]
    confidence_groups: dict[str, list[float]] = {}
    regime_groups: dict[str, list[float]] = {}
    for row in resolved:
        record = by_id[row["forecast_id"]]
        confidence = str(record.get("confidence_label") or "UNAVAILABLE")
        regime = str(record.get("regime") or "NOT_IMPLEMENTED")
        if confidence != "UNAVAILABLE":
            confidence_groups.setdefault(confidence, []).append(float(row["direction_correct"]))
        if regime != "NOT_IMPLEMENTED":
            regime_groups.setdefault(regime, []).append(float(row["direction_correct"]))
    metrics = {
        "status": "LIVE" if len(resolved) >= 20 else "INSUFFICIENT_HISTORY",
        "mode": "SHADOW_LIVE",
        "resolved_forecasts": len(resolved),
        "open_forecasts": open_count,
        "directional_accuracy": _finite_mean([float(value) for value in directional]),
        "spearman_ic": ic,
        "interval_coverage": _finite_mean(
            [float(row["inside_interval"]) for row in interval_rows]
        ),
        "median_interval_width": (
            float(np.median(interval_widths)) if interval_widths else None
        ),
        "top_decile_spread": (
            float(np.mean(top) - np.mean(bottom)) if top and bottom else None
        ),
        "calibration_by_confidence": {
            label: _finite_mean(values) for label, values in sorted(confidence_groups.items())
        },
        "hit_rate_by_regime": {
            label: _finite_mean(values) for label, values in sorted(regime_groups.items())
        },
        "net_portfolio_contribution": _finite_mean(
            [
                float(row["portfolio_contribution"]) - float(row["estimated_cost"])
                for row in resolved
            ]
        ),
        "last_20": len(resolved[-20:]),
        "last_60": len(resolved[-60:]),
        "last_120": len(resolved[-120:]),
        "model_abstention_rate": None,
        "false_positive_rebalance_rate": None,
    }
    return metrics


def prepare_ledger(
    repo: Path,
    latest: dict,
    model_card: dict,
    config: StrategyConfig,
) -> tuple[dict[str, dict], dict]:
    index = load_index(repo)
    records = [dict(row) for row in index["records"]]
    resolutions = [dict(row) for row in index["resolutions"]]
    by_id = {row["forecast_id"]: row for row in records}
    chain_head = str(index.get("chain_head") or GENESIS_HASH)
    data_cutoff = str(latest["data_as_of"])
    model_bundle_version = "adaptive-alpha-3a.1"
    dataset_version = _dataset_snapshot_hash(latest)
    feature_version = _feature_version(model_card)
    new_records: list[dict] = []
    positions = latest.get("portfolio", {}).get("positions", [])
    rank_buckets = _forecast_rank_buckets(positions)
    for position in positions:
        ticker = str(position["ticker"])
        forecast_id = _forecast_id(data_cutoff, ticker, model_bundle_version)
        body = {
            "forecast_id": forecast_id,
            "created_at": latest["generated_at"],
            "data_cutoff": data_cutoff,
            "ticker": ticker,
            "horizon_trading_days": int(latest.get("horizon_sessions", 20)),
            "point_forecast_excess_return": float(position["expected_excess_return_20d"]),
            "forecast_rank_bucket": rank_buckets[ticker],
            "lower_bound": None,
            "upper_bound": None,
            "confidence_label": "UNAVAILABLE",
            "regime": "NOT_IMPLEMENTED",
            "model_bundle_version": model_bundle_version,
            "feature_version": feature_version,
            "dataset_version": dataset_version,
            "target_weight": float(position.get("target_weight") or 0),
            "change_weight": float(position.get("change_weight") or 0),
            "status": "OPEN",
            "previous_record_hash": chain_head,
        }
        body["content_hash"] = _record_hash(chain_head, body)
        existing = by_id.get(forecast_id)
        if existing:
            immutable_keys = (
                "data_cutoff",
                "ticker",
                "point_forecast_excess_return",
                "model_bundle_version",
                "feature_version",
                "dataset_version",
            )
            conflicts = {
                key: {"stored": existing.get(key), "candidate": body.get(key)}
                for key in immutable_keys
                if existing.get(key) != body.get(key)
            }
            if conflicts:
                raise ValueError(
                    f"immutable forecast conflict: {forecast_id}: {_canonical(conflicts)}"
                )
            continue
        records.append(body)
        new_records.append(body)
        by_id[forecast_id] = body
        chain_head = body["content_hash"]

    resolved_ids = {row["forecast_id"] for row in resolutions}
    new_resolutions: list[dict] = []
    for record in records:
        if record["forecast_id"] in resolved_ids:
            continue
        resolution = _resolution_for_record(record, repo, config)
        if resolution is not None:
            resolutions.append(resolution)
            new_resolutions.append(resolution)
            resolved_ids.add(record["forecast_id"])

    metrics = live_metrics(records, resolutions)
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    index = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "mode": "SHADOW_LIVE",
        "chain_head": chain_head,
        "records": records,
        "resolutions": resolutions,
        "metrics": metrics,
        "updated_at": updated_at,
        "recent_forecasts": records[-20:],
        "recent_resolutions": resolutions[-20:],
    }
    errors = validate_ledger(index)
    if errors:
        raise ValueError("; ".join(errors))
    files = {"ledger/index.json": index}
    if records:
        for cutoff, cutoff_records in pd.DataFrame(records).groupby("data_cutoff", sort=True):
            files[f"ledger/open/{cutoff}.json"] = {
                "schema_version": LEDGER_SCHEMA_VERSION,
                "data_cutoff": cutoff,
                "records": cutoff_records.to_dict(orient="records"),
            }
    resolution_groups: dict[str, list[dict]] = {}
    for resolution in resolutions:
        resolution_groups.setdefault(str(resolution["resolved_at"])[:10], []).append(resolution)
    for resolution_date, date_resolutions in sorted(resolution_groups.items()):
        files[f"ledger/resolved/{resolution_date}.json"] = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "resolved_at": resolution_date,
            "resolutions": date_resolutions,
        }
    return files, metrics
