from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ml_strategy.config import StrategyConfig
from ml_strategy.iceemdan_features import (
    ICEEMDAN_FEATURE_COLUMNS,
    ColominasIceemdanBackend,
    build_iceemdan_feature_panel,
)


FIXTURE = Path(__file__).resolve().parents[2] / "config" / "ml_strategy" / "iceemdan_reference.json"


def test_iceemdan_port_matches_reference_numerically():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    params = fixture["parameters"]
    signal = np.asarray(fixture["signal"], dtype=float)
    noise = np.asarray(fixture["noise_matrix"], dtype=float)
    reference = np.asarray(fixture["reference_modes"], dtype=float)
    backend = ColominasIceemdanBackend(
        ensemble_size=params["ensemble_size"],
        noise_width=params["noise_width"],
        max_sift_iterations=params["max_sift_iterations"],
        max_imfs=params["max_imfs"],
        snr_mode=params["snr_mode"],
    )
    first = backend.decompose(signal, params["seed"], noise)
    second = backend.decompose(signal, params["seed"], noise)

    reconstruction = np.linalg.norm(first.sum(axis=0) - signal) / np.linalg.norm(signal)
    assert reconstruction < 1e-10
    assert np.array_equal(first, second)
    assert first.shape[0] == reference.shape[0]
    assert np.corrcoef(first[0], reference[0])[0, 1] > 0.98
    assert np.corrcoef(first[1], reference[1])[0, 1] > 0.95
    first_energy = np.square(first[0]).sum()
    reference_energy = np.square(reference[0]).sum()
    assert abs(first_energy / reference_energy - 1) < 0.10


def test_iceemdan_feature_panel_uses_only_snapshot_history(tmp_path):
    dates = pd.bdate_range("2020-01-01", periods=360)
    tickers = ["AAA", "BBB"]
    index = pd.MultiIndex.from_product([dates, tickers], names=["date", "ticker"])
    panel = pd.DataFrame({"return_1d": 0.0}, index=index)
    benchmark = pd.Series(np.arange(360, dtype=float) + 1000, index=dates)
    seen = []

    def factory(_):
        def backend(values, seed):
            seen.append((len(values), values[-1], seed))
            return {
                "iceemdan_imf1_energy_ratio": 0.1,
                "iceemdan_high_freq_energy_ratio": 0.2,
                "iceemdan_low_freq_energy_ratio": 0.3,
                "iceemdan_residue_slope_20": values[-1],
                "iceemdan_residue_slope_60": values[-1],
                "iceemdan_mode_count": 4.0,
            }

        return backend

    model_config = {
        "min_history": 64,
        "schedule_sessions": 40,
        "training_window_sessions": 160,
        "seed": 17,
    }
    result = build_iceemdan_feature_panel(
        panel,
        benchmark,
        StrategyConfig(
            min_history=64,
            training_window_sessions=160,
            evaluation_folds=3,
            rebalance_frequency_sessions=20,
        ),
        model_config,
        tmp_path,
        backend_factory=factory,
    )
    assert set(ICEEMDAN_FEATURE_COLUMNS) <= set(result)
    assert seen
    for length, last_value, seed in seen:
        expected = np.log(benchmark.iloc[length - 1])
        assert last_value == expected
        assert seed == 17
