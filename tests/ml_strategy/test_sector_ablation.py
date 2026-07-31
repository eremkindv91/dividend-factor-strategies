from __future__ import annotations

from datetime import date

from ml_strategy.config import StrategyConfig
from ml_strategy.pipeline import build_bundle


def test_ablation_uses_identical_folds_and_keeps_unapproved_features_out(market_repo):
    config = StrategyConfig(
        min_training_rows=300,
        evaluation_folds=4,
        max_universe=18,
        min_cross_section=10,
    )
    bundle = build_bundle(market_repo, config=config, today=date(2024, 12, 1))
    rows = bundle["backtest.json"]["sector_ablation"]
    assert len(rows) == 4
    assert all(row["same_folds"] for row in rows)
    assert all(row["candidate_n"] > 0 for row in rows)
    assert all("promotion_gates" in row for row in rows)
    assert all(isinstance(row["failed_gates"], list) for row in rows)
    assert all(isinstance(row["used_in_production"], bool) for row in rows)
    approved = set(bundle["model_card.json"]["sector_features"]["approved_feature_columns"])
    model_features = set(bundle["model_card.json"]["features"])
    assert approved <= model_features
    assert all(row["issuer_exposure_variant"] == "BLOCKED" for row in rows)
    quality = bundle["sector_features/latest_quality.json"]
    assert quality["evaluated_pack_count"] == 4
    assert quality["production_pack_count"] == sum(row["status"] == "APPROVED" for row in rows)
