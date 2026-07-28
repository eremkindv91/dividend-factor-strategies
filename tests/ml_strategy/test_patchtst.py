from __future__ import annotations

import numpy as np

from ml_strategy.config import StrategyConfig
from ml_strategy.data import load_market_data
from ml_strategy.features import build_feature_panel
from ml_strategy.patchtst import (
    FoldSequenceScaler,
    _build_network,
    evaluate_patchtst,
)


def test_patchtst_architecture_contains_patching_and_shared_encoder():
    torch = __import__("torch")
    config = {
        "patch_length": 8,
        "patch_stride": 4,
        "d_model": 16,
        "attention_heads": 4,
        "encoder_layers": 1,
        "dropout": 0.0,
    }
    model = _build_network(channels=3, lookback=32, config=config)
    values = torch.zeros((5, 3, 32), dtype=torch.float32)
    output = model(values)
    assert output.shape == (5,)
    assert model.patch_length == 8
    assert model.stride == 4
    assert model.patch_count == 7
    assert model.channels == 3
    assert model.position.shape == (1, 7, 16)
    assert len(model.encoder.layers) == 1


def test_patchtst_scaler_fits_train_only():
    train = np.ones((4, 2, 8), dtype=float)
    test = np.full((2, 2, 8), 100.0)
    scaler = FoldSequenceScaler().fit(train)
    transformed = scaler.transform(test)
    assert scaler.mean_.tolist() == [1.0, 1.0]
    assert np.all(transformed == 99.0)


def test_patchtst_real_walk_forward_writes_checkpoints(market_repo):
    config = StrategyConfig(
        min_history=80,
        training_window_sessions=220,
        min_training_rows=180,
        evaluation_folds=3,
        max_universe=18,
        min_cross_section=10,
        rebalance_frequency_sessions=20,
    )
    data = load_market_data(
        market_repo / "data" / "daily",
        market_repo / "data" / "security_master.json",
        market_repo / "data" / "daily" / "benchmarks" / "MCFTR.parquet",
        market_repo / "data" / "daily" / "dividends.json",
    )
    panel = build_feature_panel(data, config)
    model_config = {
        "sequence_columns": [
            "return_1d",
            "mcftr_return_20d",
            "usdrub_return_20d",
            "key_rate_pct",
        ],
        "lookback": 32,
        "patch_length": 8,
        "patch_stride": 4,
        "d_model": 16,
        "attention_heads": 4,
        "encoder_layers": 1,
        "dropout": 0.0,
        "epochs": 1,
        "batch_size": 128,
        "learning_rate": 0.001,
        "weight_decay": 0.0,
        "validation_fraction": 0.1,
        "early_stopping_patience": 1,
        "max_training_samples": 500,
        "seed": 42,
    }
    result = evaluate_patchtst(
        panel,
        config,
        model_config,
        market_repo / "data" / "ml_strategy" / "advanced" / "checkpoints",
    )
    metadata = result.execution_metadata
    assert metadata["trained"] is True
    assert metadata["mock_backend"] is False
    assert metadata["prediction_count"] > 0
    assert metadata["checkpoint_exists"] is True
    assert result.evaluation.predictions["forecast"].notna().all()
    assert all(fold["sequence_end_rule"] for fold in result.evaluation.folds)
    assert all(fold["train_end"] < fold["prediction_date"] for fold in result.evaluation.folds)
    assert all(fold["train_target_end_max"] < fold["prediction_date"] for fold in result.evaluation.folds)
    assert all(fold["purge_validated"] for fold in result.evaluation.folds)
    assert all(fold["sequence_boundary_validated"] for fold in result.evaluation.folds)
