from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import StrategyConfig
from ..data import MarketData
from ..features import FEATURE_COLUMNS
from ..models import ModelEvaluation, walk_forward
from .mapping import load_sector_mapping, pack_for_security
from .packs import PACKS
from .publication_calendar import market_series_observations, point_in_time_values
from .registry import load_config, load_source_registry
from .transformations import trailing_return

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
    prediction_times = pd.DatetimeIndex(dates).tz_localize("UTC")
    aligned = point_in_time_values(observations, prediction_times)
    aligned = aligned.set_index(pd.to_datetime(aligned["prediction_at"]).dt.tz_convert(None))
    value = pd.to_numeric(aligned["value"], errors="coerce").reindex(dates)
    available = pd.to_datetime(aligned["available_at"], utc=True).dt.tz_convert(None)
    available.index = aligned.index
    return value, available.reindex(dates)


def _assign(panel: pd.DataFrame, dates: pd.Series, mask: np.ndarray, name: str, values: pd.Series) -> None:
    panel[name] = values.reindex(pd.DatetimeIndex(dates)).to_numpy(dtype=float) * mask.astype(float)


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
    generated_at = generated_at or pd.Timestamp(datetime.now(timezone.utc))
    dates = panel.index.get_level_values("date")
    unique_dates = dates.unique().sort_values()
    tickers = panel.index.get_level_values("ticker")
    sectors = panel["sector"].astype(str)
    pack_by_ticker = {
        ticker: pack_for_security(ticker, str(data.master.get(ticker, {}).get("sector") or ""), mapping)
        for ticker in tickers.unique()
    }
    pack_values = pd.Series(tickers.map(pack_by_ticker), index=panel.index)

    source_map = {
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
                "enabled": enabled,
                "status": "RESEARCH_ONLY" if enabled else "BLOCKED",
                "ablation_status": "PENDING",
                "features": pack["features"],
                "approved_sources": pack["approved_sources"],
                "blocked_sources": blocked,
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
        "status": "DEGRADED" if any(row["blocked_sources"] for row in pack_rows) else "PASS",
        "point_in_time_policy": "available_at <= prediction_timestamp",
        "issuer_exposure_status": mapping["issuer_exposure_status"],
        "issuer_exposure_reason": mapping["issuer_exposure_reason"],
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
    base = _metric(evaluations["BASE"])
    rows: list[dict] = []
    approved_columns: list[str] = []
    minimum_ic = float(promotion["minimum_spearman_ic_improvement"])
    minimum_hit = float(promotion["minimum_hit_rate_change"])
    for pack_id, columns in result.pack_columns.items():
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
        candidate = _metric(evaluations[pack_name])
        same_n = int(candidate.get("n", 0)) == int(base.get("n", 0))
        ic_gain = (candidate.get("spearman_ic") or -1.0) - (base.get("spearman_ic") or -1.0)
        hit_gain = (candidate.get("hit_rate") or 0.0) - (base.get("hit_rate") or 0.0)
        positive_spread = (candidate.get("top_bottom_spread") or 0.0) > 0
        approved = (
            same_n
            and ic_gain >= minimum_ic
            and hit_gain >= minimum_hit
            and (
                positive_spread
                or not bool(promotion.get("require_positive_top_bottom_spread", True))
            )
        )
        if approved:
            approved_columns.extend([sector_column] + columns)
        rows.append(
            {
                "pack_id": pack_id,
                "status": "APPROVED" if approved else "RESEARCH_ONLY",
                "base_n": int(base.get("n", 0)),
                "candidate_n": int(candidate.get("n", 0)),
                "base_spearman_ic": base.get("spearman_ic"),
                "candidate_spearman_ic": candidate.get("spearman_ic"),
                "spearman_ic_improvement": ic_gain,
                "hit_rate_change": hit_gain,
                "candidate_top_bottom_spread": candidate.get("top_bottom_spread"),
                "same_folds": [
                    row["prediction_date"] for row in evaluations["BASE"].folds
                ]
                == [row["prediction_date"] for row in evaluations[pack_name].folds],
                "issuer_exposure_variant": "BLOCKED",
                "reason": (
                    "Passed fixed pre-declared promotion gates."
                    if approved
                    else "Did not pass fixed pre-declared out-of-sample promotion gates."
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
