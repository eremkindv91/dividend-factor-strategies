from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .decomposition import expanding_iceemdan_features


ICEEMDAN_FEATURE_COLUMNS = [
    "iceemdan_imf1_energy_ratio",
    "iceemdan_high_freq_energy_ratio",
    "iceemdan_low_freq_energy_ratio",
    "iceemdan_residue_slope_20",
    "iceemdan_residue_slope_60",
    "iceemdan_mode_count",
]


class IceemdanBackendUnavailable(RuntimeError):
    pass


def _emd_components(signal: np.ndarray, max_imfs: int, max_iterations: int) -> tuple[np.ndarray, np.ndarray]:
    try:
        from PyEMD import EMD
    except ImportError as exc:  # pragma: no cover - exercised in dependency-free CI
        raise IceemdanBackendUnavailable("EMD-signal/PyEMD is not installed") from exc
    emd = EMD()
    emd.MAX_ITERATION = int(max_iterations)
    emd.emd(np.asarray(signal, dtype=float), max_imf=int(max_imfs))
    imfs, residue = emd.get_imfs_and_residue()
    return np.asarray(imfs, dtype=float), np.asarray(residue, dtype=float)


def _local_mean(signal: np.ndarray, max_iterations: int) -> np.ndarray:
    _, residue = _emd_components(signal, max_imfs=1, max_iterations=max_iterations)
    return residue


def _has_decomposable_residue(signal: np.ndarray) -> bool:
    values = np.asarray(signal, dtype=float)
    if len(values) < 5 or not np.isfinite(values).all():
        return False
    delta = np.diff(values)
    signs = np.sign(delta)
    signs = signs[signs != 0]
    return bool(len(signs) >= 3 and np.count_nonzero(np.diff(signs)) >= 2)


@dataclass(frozen=True)
class ColominasIceemdanBackend:
    """ICEEMDAN port of Colominas, Schlotthauer and Torres (2014).

    PyEMD supplies only the underlying EMD operator. The ensemble/residue
    recursion below follows the authors' public ``iceemdan.m`` reference
    implementation and does not call or relabel CEEMDAN.
    """

    ensemble_size: int = 8
    noise_width: float = 0.2
    max_sift_iterations: int = 40
    max_imfs: int = 6
    snr_mode: int = 1

    def decompose(
        self,
        signal: np.ndarray,
        seed: int,
        noise_matrix: np.ndarray | None = None,
    ) -> np.ndarray:
        raw = np.asarray(signal, dtype=float).reshape(-1)
        if len(raw) < 32 or not np.isfinite(raw).all():
            raise ValueError("ICEEMDAN requires at least 32 finite observations")
        scale = float(np.std(raw))
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError("ICEEMDAN signal variance is zero")
        x = raw / scale
        rng = np.random.default_rng(int(seed))
        if noise_matrix is None:
            noise_realizations = rng.standard_normal((int(self.ensemble_size), len(x)))
        else:
            noise_realizations = np.asarray(noise_matrix, dtype=float)
            expected = (int(self.ensemble_size), len(x))
            if noise_realizations.shape != expected:
                raise ValueError(
                    f"ICEEMDAN noise matrix has shape {noise_realizations.shape}, expected {expected}"
                )
        noise_modes: list[np.ndarray] = []
        for noise in noise_realizations:
            imfs, _ = _emd_components(noise, self.max_imfs + 1, self.max_sift_iterations)
            noise_modes.append(imfs)

        first_residues: list[np.ndarray] = []
        for modes in noise_modes:
            first_noise = modes[0]
            noise_std = float(np.std(first_noise))
            if noise_std <= 0:
                continue
            perturbed = x + self.noise_width * first_noise / noise_std
            first_residues.append(_local_mean(perturbed, self.max_sift_iterations))
        if not first_residues:
            raise ValueError("ICEEMDAN produced no valid first-stage residues")

        residue = np.mean(first_residues, axis=0)
        components = [x - residue]
        mode_number = 1
        while _has_decomposable_residue(residue) and mode_number < int(self.max_imfs):
            stage_residues: list[np.ndarray] = []
            residue_std = float(np.std(residue))
            for noise_imfs in noise_modes:
                if noise_imfs.shape[0] > mode_number:
                    noise = noise_imfs[mode_number].copy()
                    if self.snr_mode == 2:
                        noise_std = float(np.std(noise))
                        if noise_std > 0:
                            noise /= noise_std
                    perturbed = residue + residue_std * self.noise_width * noise
                else:
                    perturbed = residue
                stage_residues.append(_local_mean(perturbed, self.max_sift_iterations))
            next_residue = np.mean(stage_residues, axis=0)
            components.append(residue - next_residue)
            residue = next_residue
            mode_number += 1
        components.append(residue)
        return np.vstack(components) * scale

    def __call__(self, signal: np.ndarray, seed: int) -> dict[str, float]:
        components = self.decompose(signal, seed)
        imfs, residue = components[:-1], components[-1]
        centered = np.asarray(signal, dtype=float) - float(np.mean(signal))
        total_energy = float(np.square(centered).sum())
        denominator = max(total_energy, np.finfo(float).eps)
        energies = np.square(imfs).sum(axis=1) if len(imfs) else np.array([], dtype=float)

        def slope(window: int) -> float:
            values = residue[-min(window, len(residue)) :]
            if len(values) < 3:
                return 0.0
            x = np.arange(len(values), dtype=float)
            raw_slope = float(np.polyfit(x, values, 1)[0])
            normalizer = max(float(np.std(signal)), np.finfo(float).eps)
            return raw_slope * len(values) / normalizer

        return {
            "iceemdan_imf1_energy_ratio": float(energies[0] / denominator) if len(energies) else 0.0,
            "iceemdan_high_freq_energy_ratio": float(energies[:2].sum() / denominator),
            "iceemdan_low_freq_energy_ratio": float(energies[-2:].sum() / denominator),
            "iceemdan_residue_slope_20": slope(20),
            "iceemdan_residue_slope_60": slope(60),
            "iceemdan_mode_count": float(len(imfs)),
        }


def validate_against_reference(reference_path: Path) -> dict:
    fixture = json.loads(reference_path.read_text(encoding="utf-8"))
    parameters = fixture["parameters"]
    signal = np.asarray(fixture["signal"], dtype=float)
    noise = np.asarray(fixture["noise_matrix"], dtype=float)
    reference = np.asarray(fixture["reference_modes"], dtype=float)
    backend = ColominasIceemdanBackend(
        ensemble_size=int(parameters["ensemble_size"]),
        noise_width=float(parameters["noise_width"]),
        max_sift_iterations=int(parameters["max_sift_iterations"]),
        max_imfs=int(parameters["max_imfs"]),
        snr_mode=int(parameters["snr_mode"]),
    )
    first = backend.decompose(signal, int(parameters["seed"]), noise)
    second = backend.decompose(signal, int(parameters["seed"]), noise)
    reconstruction = float(np.linalg.norm(first.sum(axis=0) - signal) / np.linalg.norm(signal))
    correlations = [
        float(np.corrcoef(first[index], reference[index])[0, 1])
        for index in range(min(len(first), len(reference)))
    ]
    imf1_energy_ratio = float(
        np.square(first[0]).sum() / max(np.square(reference[0]).sum(), np.finfo(float).eps)
    )
    checks = {
        "reconstruction_error": reconstruction < 1e-10,
        "deterministic": bool(np.array_equal(first, second)),
        "imf_count": first.shape[0] == reference.shape[0],
        "reference_imf1_correlation": correlations[0] > 0.98,
        "reference_imf2_correlation": len(correlations) > 1 and correlations[1] > 0.95,
        "reference_imf1_energy": abs(imf1_energy_ratio - 1.0) < 0.10,
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "reference": fixture["reference"],
        "checks": checks,
        "reconstruction_relative_error": reconstruction,
        "mode_count_python": int(first.shape[0]),
        "mode_count_reference": int(reference.shape[0]),
        "mode_correlations": correlations,
        "imf1_energy_ratio_to_reference": imf1_energy_ratio,
    }


def build_iceemdan_feature_panel(
    panel: pd.DataFrame,
    benchmark: pd.Series,
    strategy_config: StrategyConfig,
    model_config: dict,
    cache_dir: Path,
    backend_factory: Callable[[dict], Callable[[np.ndarray, int], dict[str, float]]] | None = None,
) -> pd.DataFrame:
    dates = panel.index.get_level_values("date").unique().sort_values()
    signal = np.log(benchmark.reindex(dates).ffill()).dropna()
    min_history = int(model_config["min_history"])
    schedule = int(model_config["schedule_sessions"])
    required_history = (
        int(model_config.get("training_window_sessions", strategy_config.training_window_sessions))
        + (strategy_config.evaluation_folds + 3) * strategy_config.rebalance_frequency_sessions
        + strategy_config.horizon
    )
    start = max(min_history - 1, len(signal) - required_history)
    prediction_dates = list(signal.index[start::schedule])
    if len(signal) and (not prediction_dates or prediction_dates[-1] != signal.index[-1]):
        prediction_dates.append(signal.index[-1])
    if backend_factory is None:
        backend = ColominasIceemdanBackend(
            ensemble_size=int(model_config["ensemble_size"]),
            noise_width=float(model_config["noise_width"]),
            max_sift_iterations=int(model_config["max_sift_iterations"]),
            max_imfs=int(model_config["max_imfs"]),
            snr_mode=int(model_config["snr_mode"]),
        )
    else:
        backend = backend_factory(model_config)
    snapshots = expanding_iceemdan_features(
        signal,
        prediction_dates,
        backend,
        cache_dir=cache_dir,
        min_history=min_history,
        seed=int(model_config["seed"]),
    )
    if snapshots.empty:
        raise ValueError("ICEEMDAN feature generation produced no snapshots")
    daily = snapshots.reindex(dates).ffill()
    out = panel.copy()
    date_index = out.index.get_level_values("date")
    for column in ICEEMDAN_FEATURE_COLUMNS:
        out[column] = daily[column].reindex(date_index).to_numpy(dtype=float)
    return out
