"""
divmodel — перенос ML-логики дивидендного прогноза из notebooks/03_ml_dividend_forecast.ipynb.

Логика и гиперпараметры скопированы из ноутбука ВКР без изменений (см. config.py, pipeline.py).
Используется офлайн-сборкой артефакта (scripts/build_artifact.py) для расчёта per-ticker SHAP,
который в самом ноутбуке считается только агрегированно.
"""
from . import config
from .pipeline import (
    load_panel,
    build_targets,
    add_dsi_proxy,
    safe_features,
    drop_correlated,
    MultiModelZI,
    per_ticker_shap,
    ensemble_shap,
)

__all__ = [
    "config",
    "load_panel",
    "build_targets",
    "add_dsi_proxy",
    "safe_features",
    "drop_correlated",
    "MultiModelZI",
    "per_ticker_shap",
    "ensemble_shap",
]
