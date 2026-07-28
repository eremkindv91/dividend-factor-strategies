from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .config import StrategyConfig
from .features import FEATURE_COLUMNS, eligible_cross_section

TARGET = "forward_excess_total_return_20d_vs_mcftr"


@dataclass
class ModelEvaluation:
    champion: str
    champion_status: str
    latest_forecasts: pd.Series
    folds: list[dict]
    predictions: pd.DataFrame
    metrics: dict[str, dict]
    challengers: list[dict]


def _safe_correlation(y_true: np.ndarray, y_pred: np.ndarray, method: str) -> float | None:
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    if (
        mask.sum() < 3
        or np.ptp(y_true[mask]) <= np.finfo(float).eps
        or np.ptp(y_pred[mask]) <= np.finfo(float).eps
    ):
        return None
    value = spearmanr(y_true[mask], y_pred[mask]).statistic if method == "spearman" else pearsonr(
        y_true[mask], y_pred[mask]
    ).statistic
    return float(value) if np.isfinite(value) else None


def prediction_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    if not mask.any():
        return {"n": 0, "mae": None, "rmse": None, "spearman_ic": None, "pearson_ic": None, "hit_rate": None}
    actual, forecast = y_true[mask], y_pred[mask]
    order = np.argsort(forecast)
    bucket = max(1, len(order) // 10)
    top = actual[order[-bucket:]]
    bottom = actual[order[:bucket]]
    return {
        "n": int(mask.sum()),
        "mae": float(np.mean(np.abs(actual - forecast))),
        "rmse": float(np.sqrt(np.mean(np.square(actual - forecast)))),
        "spearman_ic": _safe_correlation(actual, forecast, "spearman"),
        "pearson_ic": _safe_correlation(actual, forecast, "pearson"),
        "hit_rate": float(np.mean(np.sign(actual) == np.sign(forecast))),
        "top_decile_mean_excess_return": float(np.mean(top)),
        "top_bottom_spread": float(np.mean(top) - np.mean(bottom)),
    }


def _elastic_net(config: StrategyConfig) -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            (
                "model",
                ElasticNet(
                    alpha=0.001,
                    l1_ratio=0.20,
                    max_iter=10_000,
                    random_state=config.random_seed,
                ),
            ),
        ]
    )


def _ridge() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            ("model", Ridge(alpha=10.0)),
        ]
    )


def _optional_challengers(config: StrategyConfig) -> list[tuple[str, object, str | None]]:
    out: list[tuple[str, object, str | None]] = []
    try:
        from lightgbm import LGBMRegressor

        out.append(
            (
                "lightgbm",
                LGBMRegressor(
                    n_estimators=250,
                    learning_rate=0.03,
                    max_depth=4,
                    num_leaves=15,
                    min_child_samples=30,
                    subsample=0.8,
                    colsample_bytree=0.8,
                    random_state=config.random_seed,
                    verbosity=-1,
                ),
                None,
            )
        )
    except ImportError:
        out.append(("lightgbm", None, "dependency unavailable"))
    try:
        from catboost import CatBoostRegressor

        out.append(
            (
                "catboost",
                CatBoostRegressor(
                    iterations=250,
                    depth=4,
                    learning_rate=0.03,
                    loss_function="RMSE",
                    random_seed=config.random_seed,
                    verbose=False,
                    allow_writing_files=False,
                ),
                None,
            )
        )
    except ImportError:
        out.append(("catboost", None, "dependency unavailable"))
    return out


def _aggregate_metrics(predictions: pd.DataFrame) -> dict[str, dict]:
    metrics: dict[str, dict] = {}
    for model in sorted(predictions["model"].unique()):
        rows = predictions[predictions["model"] == model]
        metrics[model] = prediction_metrics(rows["actual"].to_numpy(), rows["forecast"].to_numpy())
    return metrics


def _choose_champion(metrics: dict[str, dict]) -> tuple[str, str]:
    candidates = {
        name: values
        for name, values in metrics.items()
        if name not in {"zero", "historical_mean", "momentum_12_1"}
        and values.get("n", 0) >= 50
        and values.get("spearman_ic") is not None
    }
    baseline_ic = max(
        [
            metrics.get(name, {}).get("spearman_ic") or -1.0
            for name in ("zero", "historical_mean", "momentum_12_1")
        ]
    )
    if not candidates:
        return "momentum_12_1", "DEGRADED"
    best = max(candidates, key=lambda name: candidates[name]["spearman_ic"])
    values = candidates[best]
    approved = (
        values["spearman_ic"] > max(0.0, baseline_ic + 0.01)
        and values["hit_rate"] is not None
        and values["hit_rate"] >= 0.50
    )
    return (best, "APPROVED" if approved else "RESEARCH_ONLY")


def walk_forward(
    panel: pd.DataFrame,
    config: StrategyConfig,
    include_tree_challengers: bool = False,
    feature_columns: list[str] | None = None,
    linear_model: str = "elastic_net",
    model_name: str | None = None,
    force_model: bool = False,
    checkpoint_path: Path | None = None,
) -> ModelEvaluation:
    feature_columns = feature_columns or FEATURE_COLUMNS
    linear_label = model_name or linear_model
    missing_features = sorted(set(feature_columns) - set(panel.columns))
    if missing_features:
        raise ValueError(f"feature columns missing from panel: {missing_features}")
    dates = panel.index.get_level_values("date").unique().sort_values()
    valid_dates = dates[config.min_history : -config.horizon : config.rebalance_frequency_sessions]
    if len(valid_dates) > config.evaluation_folds:
        valid_dates = valid_dates[-config.evaluation_folds :]
    if len(valid_dates) < 3:
        raise ValueError("not enough purged walk-forward dates")
    prediction_parts: list[pd.DataFrame] = []
    folds: list[dict] = []
    tree_specs = _optional_challengers(config) if include_tree_challengers else []
    challenger_status = [
        {"name": name, "status": ("EVALUATED" if model is not None else "BLOCKED"), "reason": reason}
        for name, model, reason in tree_specs
    ]

    for fold_number, prediction_date in enumerate(valid_dates, start=1):
        cross_section = eligible_cross_section(panel, prediction_date, config)
        if len(cross_section) < config.min_cross_section:
            continue
        cutoff = prediction_date - pd.Timedelta(days=int(config.training_window_sessions * 1.6))
        train = panel[
            (panel.index.get_level_values("date") >= cutoff)
            & (panel.index.get_level_values("date") < prediction_date)
            & (panel["target_end_date"] < prediction_date)
        ].dropna(subset=[TARGET])
        train = train[train["adv_20d"] >= config.min_adv_rub]
        if len(train) < config.min_training_rows:
            continue
        x_train = train[feature_columns].replace([np.inf, -np.inf], np.nan)
        y_train = train[TARGET].astype(float)
        x_test = cross_section[feature_columns].replace([np.inf, -np.inf], np.nan)
        actual = cross_section[TARGET].astype(float)
        forecasts: dict[str, np.ndarray] = {
            "zero": np.zeros(len(cross_section)),
            "historical_mean": np.full(len(cross_section), float(y_train.mean())),
            "momentum_12_1": cross_section["momentum_12_1"].fillna(0).clip(-0.5, 0.5).to_numpy() / 12.0,
        }
        if linear_model == "elastic_net":
            linear = _elastic_net(config)
        elif linear_model == "ridge":
            linear = _ridge()
        else:
            raise ValueError(f"unsupported linear model: {linear_model}")
        linear.fit(x_train, y_train)
        forecasts[linear_label] = linear.predict(x_test)
        for name, model, reason in tree_specs:
            if model is None:
                continue
            fold_imputer = SimpleImputer(strategy="median", keep_empty_features=True).fit(x_train)
            model.fit(fold_imputer.transform(x_train), y_train)
            forecasts[name] = model.predict(fold_imputer.transform(x_test))
        fold_models: dict[str, dict] = {}
        for name, forecast in forecasts.items():
            block = pd.DataFrame(
                {
                    "date": prediction_date,
                    "ticker": cross_section.index,
                    "model": name,
                    "forecast": forecast,
                    "actual": actual.to_numpy(),
                    "forward_total_return": cross_section["forward_total_return_20d"].to_numpy(),
                    "adv_20d": cross_section["adv_20d"].to_numpy(),
                    "sector": cross_section["sector"].to_numpy(),
                }
            )
            prediction_parts.append(block)
            fold_models[name] = prediction_metrics(actual.to_numpy(), np.asarray(forecast))
        folds.append(
            {
                "fold": fold_number,
                "prediction_date": prediction_date.date().isoformat(),
                "train_end": (prediction_date - pd.Timedelta(days=1)).date().isoformat(),
                "purge_rule": "target_end_date < prediction_date",
                "training_rows": int(len(train)),
                "test_rows": int(len(cross_section)),
                "metrics": fold_models,
            }
        )
    if not prediction_parts:
        raise ValueError("walk-forward produced no valid folds")
    predictions = pd.concat(prediction_parts, ignore_index=True)
    metrics = _aggregate_metrics(predictions)
    champion, status = _choose_champion(metrics)
    if force_model:
        champion, status = linear_label, "EVALUATED"

    latest_date = dates[-1]
    latest = eligible_cross_section(panel, latest_date, config)
    train = panel[
        (panel.index.get_level_values("date") < latest_date)
        & (panel["target_end_date"] < latest_date)
    ].dropna(subset=[TARGET])
    train = train[train["adv_20d"] >= config.min_adv_rub]
    if latest.empty or len(train) < config.min_training_rows:
        raise ValueError("latest inference set is unavailable")
    if champion == "momentum_12_1":
        latest_forecasts = latest["momentum_12_1"].fillna(0).clip(-0.5, 0.5) / 12.0
    else:
        model: object
        if champion == linear_label:
            model = _elastic_net(config) if linear_model == "elastic_net" else _ridge()
            model.fit(train[feature_columns].replace([np.inf, -np.inf], np.nan), train[TARGET])
            values = model.predict(latest[feature_columns].replace([np.inf, -np.inf], np.nan))
            if checkpoint_path is not None:
                import joblib

                checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                joblib.dump(
                    {
                        "model": model,
                        "feature_columns": feature_columns,
                        "model_name": linear_label,
                        "trained": True,
                        "random_seed": config.random_seed,
                    },
                    checkpoint_path,
                )
        else:
            spec = next(item for item in tree_specs if item[0] == champion)
            model = spec[1]
            imputer = SimpleImputer(strategy="median", keep_empty_features=True).fit(
                train[feature_columns].replace([np.inf, -np.inf], np.nan)
            )
            model.fit(
                imputer.transform(train[feature_columns].replace([np.inf, -np.inf], np.nan)),
                train[TARGET],
            )
            values = model.predict(
                imputer.transform(latest[feature_columns].replace([np.inf, -np.inf], np.nan))
            )
        latest_forecasts = pd.Series(values, index=latest.index, name="forecast")
    return ModelEvaluation(
        champion=champion,
        champion_status=status,
        latest_forecasts=latest_forecasts.astype(float),
        folds=folds,
        predictions=predictions,
        metrics=metrics,
        challengers=challenger_status,
    )
