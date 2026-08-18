from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ml_strategy.config import StrategyConfig
from ml_strategy.data import load_market_data
from ml_strategy.features import build_feature_panel
from ml_strategy.sector_features.mapping import load_sector_mapping, pack_for_security
from ml_strategy.sector_features.store import build_sector_features


SECTOR_INDEXES = (
    "MOEXOG", "MOEXMM", "MOEXFN", "MOEXRE", "MOEXEU",
    "MOEXCN", "MOEXIT", "MOEXTL", "MOEXTN", "MOEXCH",
)


def _build(repo):
    daily = repo / "data" / "daily"
    data = load_market_data(
        daily_root=daily,
        master_path=repo / "data" / "security_master.json",
        benchmark_path=daily / "benchmarks" / "MCFTR.parquet",
        dividends_path=daily / "dividends.json",
        macro_paths={
            name: daily / "benchmarks" / f"{name}.parquet"
            for name in ("IMOEX", "RGBI", "USDRUB", "KEY_RATE", *SECTOR_INDEXES)
        },
    )
    panel = build_feature_panel(
        data,
        StrategyConfig(min_training_rows=300, max_universe=18, min_cross_section=10),
    )
    return build_sector_features(data, panel, repo, generated_at=pd.Timestamp("2024-12-02", tz="UTC"))


def test_official_sector_index_features_are_scoped_to_the_matching_sector(market_repo):
    result = _build(market_repo)
    oil = result.panel.xs("T00", level="ticker")
    metals = result.panel.xs("T01", level="ticker")
    technology = result.panel.xs("T04", level="ticker")
    latest = oil.index.max()

    assert np.isfinite(oil.loc[latest, "oil_sector_return_20d"])
    assert np.isfinite(oil.loc[latest, "oil_sector_relative_20d"])
    assert np.isfinite(oil.loc[latest, "oil_sector_volatility_20d"])
    assert metals.loc[latest, "oil_sector_return_20d"] == 0.0
    assert metals.loc[latest, "oil_sector_index_missing"] == 0.0
    assert np.isfinite(technology.loc[latest, "it_sector_relative_20d"])
    assert oil.loc[latest, "it_sector_return_20d"] == 0.0


def test_future_sector_index_revision_cannot_change_prior_feature_rows(market_repo):
    before = _build(market_repo).panel.xs("T00", level="ticker")
    cutoff = before.index[-40]
    path = market_repo / "data" / "daily" / "benchmarks" / "MOEXOG.parquet"
    frame = pd.read_parquet(path)
    frame.loc[pd.to_datetime(frame["trade_date"]) > cutoff, "close"] *= 3.0
    frame.to_parquet(path, index=False)
    after = _build(market_repo).panel.xs("T00", level="ticker")

    columns = [
        "oil_sector_return_20d",
        "oil_sector_return_60d",
        "oil_sector_relative_20d",
        "oil_sector_volatility_20d",
    ]
    assert np.allclose(before.loc[:cutoff, columns], after.loc[:cutoff, columns], equal_nan=True)


def test_daily_builder_refreshes_every_official_sector_index():
    script = (Path(__file__).resolve().parents[2] / "scripts" / "build_ml_strategy.py").read_text(encoding="utf-8")
    for secid in SECTOR_INDEXES:
        assert f'"{secid}"' in script
    assert "optional sector input must not block the core model" in script


def test_sector_quality_reports_mapping_coverage(market_repo):
    quality = _build(market_repo).quality_payload

    assert quality["mapped_security_count"] == 18
    assert quality["unmapped_security_count"] == 0
    assert quality["mapped_security_share"] == 1.0
    assert quality["unmapped_sectors"] == []


def test_production_sector_mapping_covers_most_of_the_security_master():
    root = Path(__file__).resolve().parents[2]
    payload = json.loads((root / "data" / "security_master.json").read_text(encoding="utf-8"))
    mapping = load_sector_mapping(root / "config" / "ml_strategy" / "sector_mapping.yml")
    securities = payload["securities"]
    mapped = [
        row for row in securities
        if pack_for_security(
            str(row.get("canonical_ticker") or row.get("secid") or ""),
            str(row.get("sector") or ""),
            mapping,
        )
    ]

    assert len(mapped) / len(securities) >= 0.80


def test_missing_optional_sector_index_degrades_pack_without_breaking_pipeline(market_repo):
    (market_repo / "data" / "daily" / "benchmarks" / "MOEXOG.parquet").unlink()
    result = _build(market_repo)
    oil = next(row for row in result.pack_rows if row["pack_id"] == "OIL_AND_GAS")

    assert result.quality_payload["status"] == "DEGRADED"
    assert oil["status"] == "RESEARCH_ONLY"
    assert oil["unavailable_sources"] == ["MOEX_MOEXOG"]
    assert result.panel["oil_sector_index_missing"].sum() > 0
