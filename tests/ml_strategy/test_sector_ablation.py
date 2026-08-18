from __future__ import annotations

from datetime import date

import pandas as pd

from ml_strategy.config import StrategyConfig
from ml_strategy.models import ModelEvaluation
from ml_strategy.pipeline import build_bundle
from ml_strategy.sector_features.packs import PACKS
from ml_strategy.sector_features.store import _sector_comparison, _sector_timing_comparison


def _evaluation(forecasts: list[float], actual: list[float] | None = None) -> ModelEvaluation:
    predictions = pd.DataFrame(
        {
            "date": ["2024-01-01"] * 4,
            "ticker": ["BANK1", "BANK2", "OIL1", "OIL2"],
            "model": ["ridge"] * 4,
            "forecast": forecasts,
            "actual": actual or [0.04, -0.03, 0.02, -0.01],
        }
    )
    return ModelEvaluation("ridge", "RESEARCH_ONLY", pd.Series(dtype=float), [], predictions, {}, [])


def test_sector_comparison_ignores_predictions_from_other_industries():
    baseline = _evaluation([0.01, 0.00, -100.0, 100.0])
    candidate = _evaluation([0.05, -0.04, 100.0, -100.0])
    result = _sector_comparison(baseline, candidate, ["BANK1", "BANK2"])
    assert result["same_rows"]
    assert result["tickers"] == 2
    assert result["candidate"]["n"] == 2
    assert result["candidate"]["hit_rate"] == 1.0


def test_sector_timing_uses_sector_forecast_relative_to_the_market():
    actual = [0.04, -0.03, -0.02, -0.01]
    baseline = _evaluation([0.00, 0.00, 0.00, 0.00], actual)
    candidate = _evaluation([0.05, 0.05, -0.05, -0.05], actual)
    result = _sector_timing_comparison(baseline, candidate, ["BANK1", "BANK2"])
    assert result["same_rows"]
    assert result["dates"] == 1
    assert result["candidate"]["hit_rate"] == 1.0


def test_ablation_uses_identical_folds_and_keeps_unapproved_features_out(market_repo):
    config = StrategyConfig(
        min_training_rows=300,
        evaluation_folds=4,
        max_universe=18,
        min_cross_section=10,
    )
    bundle = build_bundle(market_repo, config=config, today=date(2024, 12, 1))
    rows = bundle["backtest.json"]["sector_ablation"]
    assert len(rows) == len(PACKS)
    assert all(row["same_folds"] for row in rows)
    assert all(row["candidate_n"] > 0 for row in rows)
    assert all("promotion_gates" in row for row in rows)
    assert all(row["evaluation_scope"] == "sector_timing" for row in rows)
    assert all(row["feature_role"] == "sector_timing" for row in rows)
    assert all(row["timing_dates"] >= 0 for row in rows)
    assert all(
        row["promotion_gates"]["rank_ic_improvement"]["scope"] == "sector_timing"
        for row in rows
    )
    assert all(row["reference_model"] == "core_plus_sector_id" for row in rows)
    assert all("sector_oos_rows" in row for row in rows)
    assert all(isinstance(row["failed_gates"], list) for row in rows)
    assert all(isinstance(row["used_in_production"], bool) for row in rows)
    approved = set(bundle["model_card.json"]["sector_features"]["approved_feature_columns"])
    model_features = set(bundle["model_card.json"]["features"])
    assert approved <= model_features
    assert all(row["issuer_exposure_variant"] == "BLOCKED" for row in rows)
    quality = bundle["sector_features/latest_quality.json"]
    assert quality["evaluated_pack_count"] == len(PACKS)
    assert quality["production_pack_count"] == sum(row["status"] == "APPROVED" for row in rows)


def test_unavailable_official_sector_index_cannot_be_promoted(market_repo):
    (market_repo / "data" / "daily" / "benchmarks" / "MOEXOG.parquet").unlink()
    config = StrategyConfig(
        min_training_rows=300,
        evaluation_folds=4,
        max_universe=18,
        min_cross_section=10,
    )
    bundle = build_bundle(market_repo, config=config, today=date(2024, 12, 1))
    oil = next(row for row in bundle["backtest.json"]["sector_ablation"] if row["pack_id"] == "OIL_AND_GAS")

    assert oil["promotion_gates"]["source_availability"]["status"] == "FAIL"
    assert "source_availability" in oil["failed_gates"]
    assert oil["status"] == "RESEARCH_ONLY"
    assert not oil["used_in_production"]
