from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .features import eligible_cross_section
from .models import TARGET, ModelEvaluation, prediction_metrics


class PatchTSTUnavailable(RuntimeError):
    pass


@dataclass
class PatchTSTExecution:
    evaluation: ModelEvaluation
    training_history: list[dict]
    checkpoint_paths: list[str]
    execution_metadata: dict


class FoldSequenceScaler:
    def __init__(self) -> None:
        self.mean_: np.ndarray | None = None
        self.scale_: np.ndarray | None = None

    def fit(self, values: np.ndarray) -> "FoldSequenceScaler":
        if values.ndim != 3:
            raise ValueError("PatchTST scaler expects [samples, channels, lookback]")
        self.mean_ = np.nanmean(values, axis=(0, 2))
        self.scale_ = np.nanstd(values, axis=(0, 2))
        self.mean_ = np.where(np.isfinite(self.mean_), self.mean_, 0.0)
        self.scale_ = np.where(np.isfinite(self.scale_) & (self.scale_ > 1e-8), self.scale_, 1.0)
        return self

    def transform(self, values: np.ndarray) -> np.ndarray:
        if self.mean_ is None or self.scale_ is None:
            raise ValueError("PatchTST scaler is not fitted")
        mean = self.mean_[None, :, None]
        scale = self.scale_[None, :, None]
        filled = np.where(np.isfinite(values), values, mean)
        return ((filled - mean) / scale).astype(np.float32)

    def to_dict(self) -> dict:
        if self.mean_ is None or self.scale_ is None:
            raise ValueError("PatchTST scaler is not fitted")
        return {"mean": self.mean_.tolist(), "scale": self.scale_.tolist()}


def _torch():
    try:
        import torch
        from torch import nn
    except ImportError as exc:  # pragma: no cover - dependency-free environments
        raise PatchTSTUnavailable("PyTorch is not installed") from exc
    return torch, nn


def _build_network(channels: int, lookback: int, config: dict):
    torch, nn = _torch()
    patch_length = int(config["patch_length"])
    stride = int(config["patch_stride"])
    patch_count = 1 + (lookback - patch_length) // stride
    if patch_count < 2:
        raise ValueError("PatchTST requires at least two patches")

    class PatchTSTNetwork(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            d_model = int(config["d_model"])
            self.patch_length = patch_length
            self.stride = stride
            self.patch_count = patch_count
            self.channels = channels
            self.patch_embedding = nn.Linear(patch_length, d_model)
            self.position = nn.Parameter(torch.zeros(1, patch_count, d_model))
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=int(config["attention_heads"]),
                dim_feedforward=d_model * 4,
                dropout=float(config["dropout"]),
                activation="gelu",
                batch_first=True,
                norm_first=True,
            )
            self.encoder = nn.TransformerEncoder(
                encoder_layer,
                num_layers=int(config["encoder_layers"]),
                enable_nested_tensor=False,
            )
            self.head = nn.Sequential(
                nn.LayerNorm(channels * d_model),
                nn.Linear(channels * d_model, d_model),
                nn.GELU(),
                nn.Dropout(float(config["dropout"])),
                nn.Linear(d_model, 1),
            )

        def forward(self, values):
            # The same embedding and Transformer weights are applied to every
            # channel independently; channels meet only in the prediction head.
            patches = values.unfold(dimension=-1, size=self.patch_length, step=self.stride)
            batch, channel_count, patch_count_local, patch_size = patches.shape
            tokens = patches.reshape(batch * channel_count, patch_count_local, patch_size)
            tokens = self.patch_embedding(tokens) + self.position[:, :patch_count_local]
            encoded = self.encoder(tokens).mean(dim=1)
            encoded = encoded.reshape(batch, channel_count, -1)
            return self.head(encoded.reshape(batch, -1)).squeeze(-1)

    return PatchTSTNetwork()


def _wide_channels(panel: pd.DataFrame, columns: list[str]) -> dict[str, pd.DataFrame]:
    missing = sorted(set(columns) - set(panel.columns))
    if missing:
        raise ValueError(f"PatchTST sequence columns missing: {missing}")
    return {
        column: panel[column].unstack("ticker").sort_index()
        for column in columns
    }


def build_sequence_arrays(
    panel: pd.DataFrame,
    rows: pd.DataFrame,
    columns: list[str],
    lookback: int,
    max_samples: int | None = None,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, list[tuple[pd.Timestamp, str]], dict]:
    wide = _wide_channels(panel, columns)
    dates = next(iter(wide.values())).index
    date_positions = {pd.Timestamp(value): index for index, value in enumerate(dates)}
    candidates: list[tuple[pd.Timestamp, str, float]] = []
    if isinstance(rows.index, pd.MultiIndex):
        iterator = zip(
            rows.index.get_level_values("date"),
            rows.index.get_level_values("ticker"),
            rows[TARGET].to_numpy(dtype=float),
        )
    else:
        row_date = pd.Timestamp(rows.attrs.get("prediction_date"))
        iterator = ((row_date, ticker, value) for ticker, value in rows[TARGET].items())
    for raw_date, raw_ticker, target in iterator:
        dt = pd.Timestamp(raw_date)
        ticker = str(raw_ticker)
        position = date_positions.get(dt)
        if position is None or position + 1 < lookback:
            continue
        if ticker not in next(iter(wide.values())).columns:
            continue
        candidates.append((dt, ticker, float(target)))
    candidates.sort(key=lambda item: (item[0], item[1]))
    if max_samples and len(candidates) > max_samples:
        rng = np.random.default_rng(int(seed))
        selected = np.sort(rng.choice(len(candidates), size=int(max_samples), replace=False))
        candidates = [candidates[index] for index in selected]
        candidates.sort(key=lambda item: (item[0], item[1]))

    sequences: list[np.ndarray] = []
    targets: list[float] = []
    keys: list[tuple[pd.Timestamp, str]] = []
    starts: list[pd.Timestamp] = []
    for dt, ticker, target in candidates:
        end = date_positions[dt] + 1
        start = end - lookback
        sequence = np.stack(
            [frame[ticker].iloc[start:end].to_numpy(dtype=float) for frame in wide.values()]
        )
        sequences.append(sequence)
        targets.append(target)
        keys.append((dt, ticker))
        starts.append(pd.Timestamp(dates[start]))
    if not sequences:
        raise ValueError("PatchTST sequence builder produced no samples")
    metadata = {
        "sample_count": len(sequences),
        "sequence_start_min": min(starts).date().isoformat(),
        "sequence_end_max": max(key[0] for key in keys).date().isoformat(),
        "lookback": int(lookback),
        "columns": columns,
    }
    return np.stack(sequences), np.asarray(targets, dtype=np.float32), keys, metadata


def _train_fold(
    x: np.ndarray,
    y: np.ndarray,
    config: dict,
    checkpoint: Path,
    seed: int,
) -> tuple[object, FoldSequenceScaler, list[dict], int]:
    torch, nn = _torch()
    torch.manual_seed(int(seed))
    np.random.seed(int(seed))
    torch.set_num_threads(max(1, min(2, torch.get_num_threads())))
    split = max(1, min(len(x) - 1, int(len(x) * (1.0 - float(config["validation_fraction"])))))
    x_train_raw, x_valid_raw = x[:split], x[split:]
    y_train, y_valid = y[:split], y[split:]
    scaler = FoldSequenceScaler().fit(x_train_raw)
    x_train = scaler.transform(x_train_raw)
    x_valid = scaler.transform(x_valid_raw)
    model = _build_network(x.shape[1], x.shape[2], config)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(config["learning_rate"]),
        weight_decay=float(config["weight_decay"]),
    )
    loss_fn = nn.HuberLoss(delta=0.05)
    generator = torch.Generator().manual_seed(int(seed))
    dataset = torch.utils.data.TensorDataset(torch.from_numpy(x_train), torch.from_numpy(y_train))
    loader = torch.utils.data.DataLoader(
        dataset,
        batch_size=int(config["batch_size"]),
        shuffle=True,
        generator=generator,
    )
    valid_x = torch.from_numpy(x_valid)
    valid_y = torch.from_numpy(y_valid)
    history: list[dict] = []
    best_loss = float("inf")
    best_state = None
    patience = 0
    epochs_run = 0
    for epoch in range(1, int(config["epochs"]) + 1):
        model.train()
        losses: list[float] = []
        for batch_x, batch_y in loader:
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(batch_x), batch_y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(float(loss.detach()))
        model.eval()
        with torch.no_grad():
            valid_loss = float(loss_fn(model(valid_x), valid_y))
        train_loss = float(np.mean(losses)) if losses else float("nan")
        history.append({"epoch": epoch, "train_loss": train_loss, "validation_loss": valid_loss})
        epochs_run = epoch
        if valid_loss < best_loss - 1e-7:
            best_loss = valid_loss
            best_state = {name: value.detach().cpu().clone() for name, value in model.state_dict().items()}
            patience = 0
        else:
            patience += 1
            if patience >= int(config["early_stopping_patience"]):
                break
    if best_state is None:
        raise RuntimeError("PatchTST training produced no checkpoint state")
    model.load_state_dict(best_state)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": best_state,
            "scaler": scaler.to_dict(),
            "history": history,
            "seed": int(seed),
            "config": config,
            "trained": True,
        },
        checkpoint,
    )
    return model, scaler, history, epochs_run


def _predict(model, scaler: FoldSequenceScaler, values: np.ndarray) -> np.ndarray:
    torch, _ = _torch()
    model.eval()
    with torch.no_grad():
        return model(torch.from_numpy(scaler.transform(values))).cpu().numpy().astype(float)


def _aggregate(predictions: pd.DataFrame) -> dict:
    return prediction_metrics(
        predictions["actual"].to_numpy(dtype=float),
        predictions["forecast"].to_numpy(dtype=float),
    )


def evaluate_patchtst(
    panel: pd.DataFrame,
    strategy_config: StrategyConfig,
    model_config: dict,
    checkpoint_root: Path,
) -> PatchTSTExecution:
    columns = list(model_config["sequence_columns"])
    lookback = int(model_config["lookback"])
    dates = panel.index.get_level_values("date").unique().sort_values()
    valid_dates = dates[
        strategy_config.min_history : -strategy_config.horizon : strategy_config.rebalance_frequency_sessions
    ]
    if len(valid_dates) > strategy_config.evaluation_folds:
        valid_dates = valid_dates[-strategy_config.evaluation_folds :]
    prediction_parts: list[pd.DataFrame] = []
    folds: list[dict] = []
    all_history: list[dict] = []
    checkpoints: list[str] = []
    for fold_number, prediction_date in enumerate(valid_dates, start=1):
        cross_section = eligible_cross_section(panel, prediction_date, strategy_config)
        if len(cross_section) < strategy_config.min_cross_section:
            continue
        cross_section.attrs["prediction_date"] = pd.Timestamp(prediction_date)
        cutoff = prediction_date - pd.Timedelta(days=int(strategy_config.training_window_sessions * 1.6))
        train = panel[
            (panel.index.get_level_values("date") >= cutoff)
            & (panel.index.get_level_values("date") < prediction_date)
            & (panel["target_end_date"] < prediction_date)
        ].dropna(subset=[TARGET])
        train = train[train["adv_20d"] >= strategy_config.min_adv_rub]
        if len(train) < strategy_config.min_training_rows:
            continue
        seed = int(model_config["seed"]) + fold_number
        x_train, y_train, train_keys, train_meta = build_sequence_arrays(
            panel,
            train,
            columns,
            lookback,
            max_samples=int(model_config["max_training_samples"]),
            seed=seed,
        )
        x_test, _, test_keys, test_meta = build_sequence_arrays(
            panel,
            cross_section,
            columns,
            lookback,
            seed=seed,
        )
        prediction_ts = pd.Timestamp(prediction_date)
        train_sample_end = max(key[0] for key in train_keys)
        train_index = pd.MultiIndex.from_tuples(train_keys, names=panel.index.names)
        train_target_end = pd.to_datetime(
            panel["target_end_date"].reindex(train_index)
        ).max()
        test_dates = {pd.Timestamp(key[0]) for key in test_keys}
        if train_sample_end >= prediction_ts:
            raise RuntimeError("PatchTST train sequence reaches the prediction date")
        if pd.isna(train_target_end) or train_target_end >= prediction_ts:
            raise RuntimeError("PatchTST target crosses the purge boundary")
        if test_dates != {prediction_ts}:
            raise RuntimeError("PatchTST OOS sequence is not aligned to the prediction date")
        checkpoint = checkpoint_root / f"fold_{fold_number:02d}_{pd.Timestamp(prediction_date).date()}.pt"
        model, scaler, history, epochs_run = _train_fold(x_train, y_train, model_config, checkpoint, seed)
        forecast = _predict(model, scaler, x_test)
        actual = np.asarray([cross_section.at[ticker, TARGET] for _, ticker in test_keys], dtype=float)
        forward_total = np.asarray(
            [cross_section.at[ticker, "forward_total_return_20d"] for _, ticker in test_keys],
            dtype=float,
        )
        prediction_parts.append(
            pd.DataFrame(
                {
                    "date": pd.Timestamp(prediction_date),
                    "ticker": [ticker for _, ticker in test_keys],
                    "model": "patchtst",
                    "forecast": forecast,
                    "actual": actual,
                    "forward_total_return": forward_total,
                    "adv_20d": [cross_section.at[ticker, "adv_20d"] for _, ticker in test_keys],
                    "sector": [cross_section.at[ticker, "sector"] for _, ticker in test_keys],
                }
            )
        )
        history_rows = [
            {"fold": fold_number, "prediction_date": prediction_date.date().isoformat(), **row}
            for row in history
        ]
        all_history.extend(history_rows)
        checkpoints.append(str(checkpoint))
        folds.append(
            {
                "fold": fold_number,
                "prediction_date": prediction_date.date().isoformat(),
                "train_end": max(key[0] for key in train_keys).date().isoformat(),
                "train_target_end_max": pd.Timestamp(train_target_end).date().isoformat(),
                "purge_validated": True,
                "sequence_boundary_validated": True,
                "purge_rule": "target_end_date < prediction_date",
                "sequence_end_rule": "sequence_end <= sample_date < prediction_date",
                "training_rows": int(len(train_keys)),
                "test_rows": int(len(test_keys)),
                "epochs_run": int(epochs_run),
                "checkpoint": str(checkpoint),
                "train_sequence": train_meta,
                "test_sequence": test_meta,
                "scaler_fit_scope": "train_fold_only",
            }
        )
    if not prediction_parts:
        raise RuntimeError("PatchTST walk-forward produced no OOS predictions")
    predictions = pd.concat(prediction_parts, ignore_index=True)
    metrics = {"patchtst": _aggregate(predictions)}

    latest_date = dates[-1]
    latest = eligible_cross_section(panel, latest_date, strategy_config)
    latest.attrs["prediction_date"] = pd.Timestamp(latest_date)
    train = panel[
        (panel.index.get_level_values("date") < latest_date)
        & (panel["target_end_date"] < latest_date)
    ].dropna(subset=[TARGET])
    train = train[train["adv_20d"] >= strategy_config.min_adv_rub]
    latest_seed = int(model_config["seed"]) + 10_000
    x_train, y_train, _, _ = build_sequence_arrays(
        panel,
        train,
        columns,
        lookback,
        max_samples=int(model_config["max_training_samples"]),
        seed=latest_seed,
    )
    x_latest, _, latest_keys, _ = build_sequence_arrays(
        panel,
        latest,
        columns,
        lookback,
        seed=latest_seed,
    )
    latest_checkpoint = checkpoint_root / "latest.pt"
    model, scaler, history, epochs_run = _train_fold(
        x_train,
        y_train,
        model_config,
        latest_checkpoint,
        latest_seed,
    )
    checkpoints.append(str(latest_checkpoint))
    all_history.extend(
        {"fold": "latest", "prediction_date": latest_date.date().isoformat(), **row}
        for row in history
    )
    latest_values = _predict(model, scaler, x_latest)
    latest_forecasts = pd.Series(
        latest_values,
        index=[ticker for _, ticker in latest_keys],
        name="forecast",
        dtype=float,
    )
    checkpoint_hash = hashlib.sha256(latest_checkpoint.read_bytes()).hexdigest()
    evaluation = ModelEvaluation(
        champion="patchtst",
        champion_status="EVALUATED",
        latest_forecasts=latest_forecasts,
        folds=folds,
        predictions=predictions,
        metrics=metrics,
        challengers=[],
    )
    return PatchTSTExecution(
        evaluation=evaluation,
        training_history=all_history,
        checkpoint_paths=checkpoints,
        execution_metadata={
            "execution_mode": "real_training",
            "trained": True,
            "mock_backend": False,
            "backend": "pytorch_patchtst",
            "checkpoint": str(latest_checkpoint),
            "checkpoint_sha256": checkpoint_hash,
            "checkpoint_exists": latest_checkpoint.exists(),
            "folds": len(folds),
            "prediction_count": int(len(predictions)),
            "latest_epochs_run": int(epochs_run),
            "seed": int(model_config["seed"]),
            "architecture": {
                "lookback": lookback,
                "channels": columns,
                "patch_length": int(model_config["patch_length"]),
                "stride": int(model_config["patch_stride"]),
                "channel_independence": True,
                "positional_encoding": "learned",
                "encoder": "torch.nn.TransformerEncoder",
                "d_model": int(model_config["d_model"]),
                "attention_heads": int(model_config["attention_heads"]),
                "encoder_layers": int(model_config["encoder_layers"]),
            },
        },
    )
