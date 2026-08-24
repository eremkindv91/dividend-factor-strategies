from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import StrategyConfig
from ..data import MarketData
from ..features import FEATURE_COLUMNS
from ..models import ModelEvaluation, prediction_metrics, walk_forward
from .mapping import load_sector_mapping, pack_for_security
from .packs import PACKS
from .publication_calendar import market_series_observations, point_in_time_values
from .registry import load_config, load_source_registry
from .transformations import trailing_return, trailing_volatility

SECTOR_ID_COLUMNS = [f"sector_id__{pack.lower()}" for pack in PACKS]


@dataclass
class SectorFeatureResult:
    panel: pd.DataFrame
    pack_columns: dict[str, list[str]]
    pack_rows: list[dict]
    registry_payload: dict
    quality_payload: dict


def _wide_pit_series(
    values: pd.Series,
    series_id: str,
    dates: pd.DatetimeIndex,
    lag: int,
    generated_at: pd.Timestamp,
) -> tuple[pd.Series, pd.Series]:
    observations = market_series_observations(series_id, values, lag, generated_at)
    if observations.empty:
        empty_values = pd.Series(np.nan, index=dates, dtype=float)
        empty_available = pd.Series(pd.NaT, index=dates, dtype="datetime64[ns]")
        return empty_values, empty_available
    prediction_times = pd.DatetimeIndex(dates).tz_localize("UTC")
    aligned = point_in_time_values(observations, prediction_times)
    aligned = aligned.set_index(pd.to_datetime(aligned["prediction_at"]).dt.tz_convert(None))
    value = pd.to_numeric(aligned["value"], errors="coerce").reindex(dates)
    available = pd.to_datetime(aligned["available_at"], utc=True).dt.tz_convert(None)
    available.index = aligned.index
    return value, available.reindex(dates)


def _assign(panel: pd.DataFrame, dates: pd.Series, mask: np.ndarray, name: str, values: pd.Series) -> None:
    aligned = values.reindex(pd.DatetimeIndex(dates)).to_numpy(dtype=float)
    panel[name] = np.where(mask.astype(bool), aligned, 0.0)


def build_sector_features(
    data: MarketData,
    panel: pd.DataFrame,
    repo: Path,
    generated_at: pd.Timestamp | None = None,
) -> SectorFeatureResult:
    config_root = repo / "config" / "ml_strategy"
    registry_version, registry = load_source_registry(config_root / "sector_sources.yml")
    mapping = load_sector_mapping(config_root / "sector_mapping.yml")
    flags = load_config(config_root / "sector_feature_flags.yml")
    lags = load_config(config_root / "sector_release_lags.yml")
    max_sector_index_age_days = int(
        flags.get("promotion", {}).get("maximum_sector_index_age_calendar_days", 10)
    )
    generated_at = generated_at or pd.Timestamp(datetime.now(timezone.utc))
    dates = panel.index.get_level_values("date")
    unique_dates = dates.unique().sort_values()
    panel_asof = pd.Timestamp(unique_dates.max()).tz_localize(None).normalize()
    tickers = panel.index.get_level_values("ticker")
    sectors = panel["sector"].astype(str)
    pack_by_ticker = {
        ticker: pack_for_security(ticker, str(data.master.get(ticker, {}).get("sector") or ""), mapping)
        for ticker in tickers.unique()
    }
    pack_values = pd.Series(tickers.map(pack_by_ticker), index=panel.index)

    source_map = {
        "MOEX_IMOEX": data.macro.get("IMOEX", pd.Series(dtype=float)),
        "MOEX_MOEXOG": data.macro.get("MOEXOG", pd.Series(dtype=float)),
        "MOEX_MOEXMM": data.macro.get("MOEXMM", pd.Series(dtype=float)),
        "MOEX_MOEXFN": data.macro.get("MOEXFN", pd.Series(dtype=float)),
        "MOEX_MOEXRE": data.macro.get("MOEXRE", pd.Series(dtype=float)),
        "MOEX_MOEXEU": data.macro.get("MOEXEU", pd.Series(dtype=float)),
        "MOEX_MOEXCN": data.macro.get("MOEXCN", pd.Series(dtype=float)),
        "MOEX_MOEXIT": data.macro.get("MOEXIT", pd.Series(dtype=float)),
        "MOEX_MOEXTL": data.macro.get("MOEXTL", pd.Series(dtype=float)),
        "MOEX_MOEXTN": data.macro.get("MOEXTN", pd.Series(dtype=float)),
        "MOEX_MOEXCH": data.macro.get("MOEXCH", pd.Series(dtype=float)),
        "MOEX_USDRUB": data.macro.get("USDRUB", pd.Series(dtype=float)),
        "MOEX_RGBI": data.macro.get("RGBI", pd.Series(dtype=float)),
        "CBR_KEY_RATE": data.macro.get("KEY_RATE", pd.Series(dtype=float)),
    }
    aligned: dict[str, pd.Series] = {}
    available: dict[str, pd.Series] = {}
    for source_id, source in source_map.items():
        lag = int(lags.get("series", {}).get(source_id, lags.get("default_calendar_days", 1)))
        aligned[source_id], available[source_id] = _wide_pit_series(
            source, source_id, unique_dates, lag, generated_at
        )

    out = panel.copy()
    for pack in PACKS:
        out[f"sector_id__{pack.lower()}"] = (pack_values == pack).astype(float)
    usd_return = trailing_return(aligned["MOEX_USDRUB"], 20)
    rgbi_return = trailing_return(aligned["MOEX_RGBI"], 20)
    key_rate = aligned["CBR_KEY_RATE"] / 100.0
    key_rate_change = key_rate.diff(60)
    masks = {pack: (pack_values == pack).to_numpy() for pack in PACKS}
    market_return_20d = trailing_return(aligned["MOEX_IMOEX"], 20)
    for pack_id, pack in PACKS.items():
        prefix = str(pack["feature_prefix"])
        sector_index = aligned[str(pack["sector_index_source"])]
        sector_return_20d = trailing_return(sector_index, 20)
        _assign(out, dates, masks[pack_id], f"{prefix}_sector_return_20d", sector_return_20d)
        _assign(out, dates, masks[pack_id], f"{prefix}_sector_return_60d", trailing_return(sector_index, 60))
        _assign(
            out,
            dates,
            masks[pack_id],
            f"{prefix}_sector_relative_20d",
            sector_return_20d - market_return_20d,
        )
        _assign(
            out,
            dates,
            masks[pack_id],
            f"{prefix}_sector_volatility_20d",
            trailing_volatility(sector_index, 20),
        )
        out[f"{prefix}_sector_index_missing"] = (
            masks[pack_id]
            & out[
                [
                    f"{prefix}_sector_return_20d",
                    f"{prefix}_sector_return_60d",
                    f"{prefix}_sector_relative_20d",
                    f"{prefix}_sector_volatility_20d",
                ]
            ].isna().any(axis=1).to_numpy()
        ).astype(float)
    _assign(out, dates, masks["OIL_AND_GAS"], "oil_fx_driver", usd_return)
    _assign(out, dates, masks["STEEL_AND_FERROUS_METALS"], "steel_fx_driver", usd_return)
    _assign(out, dates, masks["BANKS_AND_FINANCIALS"], "bank_key_rate_level", key_rate)
    _assign(out, dates, masks["BANKS_AND_FINANCIALS"], "bank_key_rate_change_60d", key_rate_change)
    _assign(out, dates, masks["BANKS_AND_FINANCIALS"], "bank_rgbi_driver", rgbi_return)
    _assign(out, dates, masks["REAL_ESTATE_DEVELOPERS"], "developer_key_rate_level", key_rate)
    _assign(out, dates, masks["REAL_ESTATE_DEVELOPERS"], "developer_key_rate_change_60d", key_rate_change)
    _assign(out, dates, masks["REAL_ESTATE_DEVELOPERS"], "developer_rgbi_driver", rgbi_return)
    out["oil_fx_driver_missing"] = (
        masks["OIL_AND_GAS"] & out["oil_fx_driver"].isna().to_numpy()
    ).astype(float)
    out["steel_fx_driver_missing"] = (
        masks["STEEL_AND_FERROUS_METALS"] & out["steel_fx_driver"].isna().to_numpy()
    ).astype(float)
    out["bank_macro_missing"] = (
        masks["BANKS_AND_FINANCIALS"]
        & out[["bank_key_rate_level", "bank_rgbi_driver"]].isna().any(axis=1).to_numpy()
    ).astype(float)
    out["developer_macro_missing"] = (
        masks["REAL_ESTATE_DEVELOPERS"]
        & out[["developer_key_rate_level", "developer_rgbi_driver"]].isna().any(axis=1).to_numpy()
    ).astype(float)

    pack_rows: list[dict] = []
    for pack_id, pack in PACKS.items():
        source_rows = [registry[source_id] for source_id in pack["approved_sources"]]
        blocked = [source_id for source_id in pack["blocked_sources"] if registry[source_id].status == "BLOCKED"]
        unavailable = [
            source.series_id
            for source in source_rows
            if aligned.get(source.series_id, pd.Series(dtype=float)).dropna().empty
        ]
        sector_source_id = str(pack["sector_index_source"])
        sector_observations = source_map.get(sector_source_id, pd.Series(dtype=float)).dropna()
        latest_sector_observed = (
            pd.Timestamp(sector_observations.index.max()).tz_localize(None).normalize()
            if not sector_observations.empty
            else None
        )
        sector_index_age_days = (
            int((panel_asof - latest_sector_observed).days)
            if latest_sector_observed is not None
            else None
        )
        stale = (
            [sector_source_id]
            if sector_index_age_days is not None
            and sector_index_age_days > max_sector_index_age_days
            else []
        )
        latest_dates = [
            available[source.series_id].dropna().max()
            for source in source_rows
            if not available[source.series_id].dropna().empty
        ]
        latest_available = min(latest_dates) if latest_dates else None
        enabled = bool(flags["flags"].get(pack_id, {}).get("enabled"))
        pack_rows.append(
            {
                "pack_id": pack_id,
                "label": pack["label"],
                "feature_role": pack.get("feature_role", "issuer_ranking"),
                "enabled": enabled,
                "status": "RESEARCH_ONLY" if enabled else "BLOCKED",
                "ablation_status": "PENDING",
                "features": pack["features"],
                "approved_sources": pack["approved_sources"],
                "blocked_sources": blocked,
                "unavailable_sources": unavailable,
                "stale_sources": stale,
                "latest_sector_index_at": (
                    latest_sector_observed.date().isoformat()
                    if latest_sector_observed is not None
                    else None
                ),
                "sector_index_age_days": sector_index_age_days,
                "latest_available_at": latest_available.date().isoformat() if latest_available is not None else None,
                "reason": pack["reason"],
            }
        )

    source_payload = {
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "registry_version": registry_version,
        "sources": [
            {
                "series_id": spec.series_id,
                "label": spec.label,
                "provider": spec.provider,
                "source_url": spec.source_url,
                "status": spec.status,
                "revision_policy": spec.revision_policy,
                "reason": spec.reason,
            }
            for spec in registry.values()
        ],
    }
    quality_payload = {
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "status": "DEGRADED"
        if any(
            row["blocked_sources"] or row["unavailable_sources"] or row["stale_sources"]
            for row in pack_rows
        )
        else "PASS",
        "point_in_time_policy": "available_at <= prediction_timestamp",
        "maximum_sector_index_age_calendar_days": max_sector_index_age_days,
        "issuer_exposure_status": mapping["issuer_exposure_status"],
        "issuer_exposure_reason": mapping["issuer_exposure_reason"],
        "mapped_security_count": sum(pack is not None for pack in pack_by_ticker.values()),
        "unmapped_security_count": sum(pack is None for pack in pack_by_ticker.values()),
        "mapped_security_share": (
            sum(pack is not None for pack in pack_by_ticker.values()) / max(1, len(pack_by_ticker))
        ),
        "unmapped_sectors": sorted({
            str(data.master.get(ticker, {}).get("sector") or "Не определён")
            for ticker, pack in pack_by_ticker.items()
            if pack is None
        }),
        "packs": pack_rows,
    }
    return SectorFeatureResult(
        panel=out,
        pack_columns={pack_id: list(pack["features"]) for pack_id, pack in PACKS.items()},
        pack_rows=pack_rows,
        registry_payload=source_payload,
        quality_payload=quality_payload,
    )


def _metric(evaluation: ModelEvaluation) -> dict:
    return evaluation.metrics.get("ridge", {})


def _ridge_predictions(evaluation: ModelEvaluation) -> pd.DataFrame:
    return evaluation.predictions[evaluation.predictions["model"] == "ridge"].copy()


def _cross_sectional_metrics(rows: pd.DataFrame, forecast_column: str) -> dict:
    if rows.empty:
        return prediction_metrics(np.array([]), np.array([]))
    aggregate = prediction_metrics(rows["actual_baseline"].to_numpy(), rows[forecast_column].to_numpy())
    dated: list[dict] = []
    for _, cross_section in rows.groupby("date", sort=True):
        metric = prediction_metrics(
            cross_section["actual_baseline"].to_numpy(),
            cross_section[forecast_column].to_numpy(),
        )
        if metric.get("spearman_ic") is not None:
            dated.append(metric)
    if not dated:
        aggregate.update(
            {
                "spearman_ic": None,
                "pearson_ic": None,
                "top_bottom_spread": None,
                "ic_dates": 0,
                "rank_ic_std": None,
                "rank_ic_positive_rate": None,
            }
        )
        return aggregate
    rank_ics = np.asarray([row["spearman_ic"] for row in dated], dtype=float)
    pearson_ics = [row["pearson_ic"] for row in dated if row.get("pearson_ic") is not None]
    spreads = [row["top_bottom_spread"] for row in dated if row.get("top_bottom_spread") is not None]
    aggregate.update(
        {
            "spearman_ic": float(rank_ics.mean()),
            "pearson_ic": float(np.mean(pearson_ics)) if pearson_ics else None,
            "top_bottom_spread": float(np.mean(spreads)) if spreads else None,
            "ic_dates": len(dated),
            "rank_ic_std": float(rank_ics.std(ddof=1)) if len(rank_ics) > 1 else 0.0,
            "rank_ic_positive_rate": float(np.mean(rank_ics > 0)),
        }
    )
    return aggregate


def _sector_comparison(
    baseline: ModelEvaluation,
    candidate: ModelEvaluation,
    sector_tickers: list[str],
) -> dict:
    keys = ["date", "ticker"]
    baseline_rows = _ridge_predictions(baseline)
    candidate_rows = _ridge_predictions(candidate)
    baseline_rows = baseline_rows[baseline_rows["ticker"].isin(sector_tickers)]
    candidate_rows = candidate_rows[candidate_rows["ticker"].isin(sector_tickers)]
    aligned = baseline_rows.merge(
        candidate_rows,
        on=keys,
        how="inner",
        suffixes=("_baseline", "_candidate"),
        validate="one_to_one",
    )
    same_rows = (
        len(aligned) == len(baseline_rows) == len(candidate_rows)
        and set(map(tuple, baseline_rows[keys].to_numpy()))
        == set(map(tuple, candidate_rows[keys].to_numpy()))
    )
    if aligned.empty:
        baseline_metrics = _cross_sectional_metrics(aligned, "forecast_baseline")
        candidate_metrics = _cross_sectional_metrics(aligned, "forecast_candidate")
    else:
        if not np.allclose(
            aligned["actual_baseline"].to_numpy(),
            aligned["actual_candidate"].to_numpy(),
            equal_nan=True,
        ):
            raise ValueError("sector ablation actual returns differ on the common OOS rows")
        baseline_metrics = _cross_sectional_metrics(aligned, "forecast_baseline")
        candidate_metrics = _cross_sectional_metrics(aligned, "forecast_candidate")
    return {
        "same_rows": same_rows,
        "tickers": int(aligned["ticker"].nunique()) if not aligned.empty else 0,
        "dates": int(candidate_metrics.get("ic_dates", 0)),
        "baseline": baseline_metrics,
        "candidate": candidate_metrics,
    }


def _sector_timing_comparison(
    baseline: ModelEvaluation,
    candidate: ModelEvaluation,
    sector_tickers: list[str],
) -> dict:
    keys = ["date", "ticker"]
    baseline_rows = _ridge_predictions(baseline)
    candidate_rows = _ridge_predictions(candidate)
    aligned = baseline_rows.merge(
        candidate_rows,
        on=keys,
        how="inner",
        suffixes=("_baseline", "_candidate"),
        validate="one_to_one",
    )
    same_rows = (
        len(aligned) == len(baseline_rows) == len(candidate_rows)
        and set(map(tuple, baseline_rows[keys].to_numpy()))
        == set(map(tuple, candidate_rows[keys].to_numpy()))
    )
    dated: list[dict] = []
    for prediction_date, cross_section in aligned.groupby("date", sort=True):
        sector = cross_section[cross_section["ticker"].isin(sector_tickers)]
        if sector.empty:
            continue
        dated.append(
            {
                "date": prediction_date,
                "actual": float(sector["actual_baseline"].mean() - cross_section["actual_baseline"].mean()),
                "forecast_baseline": float(
                    sector["forecast_baseline"].mean() - cross_section["forecast_baseline"].mean()
                ),
                "forecast_candidate": float(
                    sector["forecast_candidate"].mean() - cross_section["forecast_candidate"].mean()
                ),
            }
        )
    frame = pd.DataFrame(dated)
    if frame.empty:
        empty = prediction_metrics(np.array([]), np.array([]))
        return {"same_rows": same_rows, "dates": 0, "baseline": empty, "candidate": empty}
    actual = frame["actual"].to_numpy()
    return {
        "same_rows": same_rows,
        "dates": len(frame),
        "baseline": prediction_metrics(actual, frame["forecast_baseline"].to_numpy()),
        "candidate": prediction_metrics(actual, frame["forecast_candidate"].to_numpy()),
    }


def evaluate_sector_ablation(
    result: SectorFeatureResult,
    config: StrategyConfig,
    promotion: dict,
) -> tuple[list[dict], list[str], dict[str, ModelEvaluation]]:
    evaluations: dict[str, ModelEvaluation] = {
        "BASE": walk_forward(
            result.panel, config, feature_columns=FEATURE_COLUMNS, linear_model="ridge"
        )
    }
    rows: list[dict] = []
    approved_columns: list[str] = []
    minimum_ic = float(promotion["minimum_spearman_ic_improvement"])
    minimum_hit = float(promotion["minimum_hit_rate_change"])
    for pack_id, columns in result.pack_columns.items():
        pack_role = PACKS[pack_id].get("feature_role", "issuer_ranking")
        sector_column = f"sector_id__{pack_id.lower()}"
        id_name = f"{pack_id}:SECTOR_ID"
        pack_name = f"{pack_id}:SECTOR_FEATURES"
        evaluations[id_name] = walk_forward(
            result.panel,
            config,
            feature_columns=FEATURE_COLUMNS + [sector_column],
            linear_model="ridge",
        )
        evaluations[pack_name] = walk_forward(
            result.panel,
            config,
            feature_columns=FEATURE_COLUMNS + [sector_column] + columns,
            linear_model="ridge",
        )
        reference = _metric(evaluations[id_name])
        candidate = _metric(evaluations[pack_name])
        same_n = int(candidate.get("n", 0)) == int(reference.get("n", 0))
        indicator = result.panel[sector_column]
        sector_tickers = sorted(
            indicator[indicator > 0.5].index.get_level_values("ticker").unique().astype(str)
        )
        sector = _sector_comparison(
            evaluations[id_name], evaluations[pack_name], sector_tickers
        )
        timing = _sector_timing_comparison(
            evaluations[id_name], evaluations[pack_name], sector_tickers
        )
        sector_base = sector["baseline"]
        sector_candidate = sector["candidate"]
        ic_gain = (sector_candidate.get("spearman_ic") or -1.0) - (
            sector_base.get("spearman_ic") or -1.0
        )
        hit_gain = (sector_candidate.get("hit_rate") or 0.0) - (
            sector_base.get("hit_rate") or 0.0
        )
        positive_spread = (sector_candidate.get("top_bottom_spread") or 0.0) > 0
        common_evidence = (
            sector_candidate.get("n", 0) >= int(promotion.get("minimum_sector_oos_rows", 60))
            and sector["tickers"] >= int(promotion.get("minimum_sector_tickers", 3))
        )
        timing_base = timing["baseline"]
        timing_candidate = timing["candidate"]
        timing_ic_gain = (timing_candidate.get("spearman_ic") or -1.0) - (
            timing_base.get("spearman_ic") or -1.0
        )
        timing_hit_gain = (timing_candidate.get("hit_rate") or 0.0) - (
            timing_base.get("hit_rate") or 0.0
        )
        timing_approved = (
            same_n
            and timing["same_rows"]
            and common_evidence
            and timing["dates"] >= int(promotion.get("minimum_timing_dates", 12))
            and (timing_candidate.get("spearman_ic") or -1.0) >= float(
                promotion.get("minimum_timing_ic", 0.0)
            )
            and timing_ic_gain >= float(promotion.get("minimum_timing_ic_improvement", 0.05))
            and timing_hit_gain >= minimum_hit
            and (
                (timing_candidate.get("top_bottom_spread") or 0.0) > 0
                or not bool(promotion.get("require_positive_top_bottom_spread", True))
            )
        )
        issuer_ranking_approved = (
            same_n
            and sector["same_rows"]
            and common_evidence
            and sector["dates"] >= int(promotion.get("minimum_sector_dates", 3))
            and ic_gain >= minimum_ic
            and hit_gain >= minimum_hit
            and (
                positive_spread
                or not bool(promotion.get("require_positive_top_bottom_spread", True))
            )
        )
        approved = timing_approved if pack_role == "sector_timing" else issuer_ranking_approved
        if approved:
            approved_columns.extend([sector_column] + columns)
        rows.append(
            {
                "pack_id": pack_id,
                "status": "APPROVED" if approved else "RESEARCH_ONLY",
                "feature_role": pack_role,
                "evaluation_scope": "sector_timing" if pack_role == "sector_timing" else "sector_only",
                "reference_model": "core_plus_sector_id",
                "base_n": int(reference.get("n", 0)),
                "candidate_n": int(candidate.get("n", 0)),
                "base_spearman_ic": sector_base.get("spearman_ic"),
                "candidate_spearman_ic": sector_candidate.get("spearman_ic"),
                "spearman_ic_improvement": ic_gain,
                "hit_rate_change": hit_gain,
                "candidate_top_bottom_spread": sector_candidate.get("top_bottom_spread"),
                "sector_oos_rows": int(sector_candidate.get("n", 0)),
                "sector_oos_tickers": sector["tickers"],
                "sector_oos_dates": sector["dates"],
                "sector_same_rows": sector["same_rows"],
                "minimum_sector_oos_rows": int(promotion.get("minimum_sector_oos_rows", 60)),
                "minimum_sector_tickers": int(promotion.get("minimum_sector_tickers", 3)),
                "minimum_sector_dates": int(promotion.get("minimum_sector_dates", 3)),
                "global_base_spearman_ic": reference.get("spearman_ic"),
                "global_candidate_spearman_ic": candidate.get("spearman_ic"),
                "global_spearman_ic_change": (
                    (candidate.get("spearman_ic") or -1.0)
                    - (reference.get("spearman_ic") or -1.0)
                ),
                "timing_dates": timing["dates"],
                "timing_base_spearman_ic": timing_base.get("spearman_ic"),
                "timing_candidate_spearman_ic": timing_candidate.get("spearman_ic"),
                "timing_spearman_ic_improvement": timing_ic_gain,
                "timing_hit_rate_change": timing_hit_gain,
                "timing_candidate_top_bottom_spread": timing_candidate.get("top_bottom_spread"),
                "minimum_timing_dates": int(promotion.get("minimum_timing_dates", 12)),
                "minimum_timing_ic": float(promotion.get("minimum_timing_ic", 0.0)),
                "minimum_timing_ic_improvement": float(
                    promotion.get("minimum_timing_ic_improvement", 0.05)
                ),
                "same_folds": [
                    row["prediction_date"] for row in evaluations[id_name].folds
                ]
                == [row["prediction_date"] for row in evaluations[pack_name].folds],
                "issuer_exposure_variant": "BLOCKED",
                "reason": (
                    "Passed fixed role-aware out-of-sample promotion gates."
                    if approved
                    else "Did not pass fixed role-aware out-of-sample promotion gates."
                ),
            }
        )
    combined_columns = SECTOR_ID_COLUMNS + [
        column for columns in result.pack_columns.values() for column in columns
    ]
    evaluations["COMBINED"] = walk_forward(
        result.panel,
        config,
        feature_columns=FEATURE_COLUMNS + combined_columns,
        linear_model="ridge",
    )
    return rows, list(dict.fromkeys(approved_columns)), evaluations
