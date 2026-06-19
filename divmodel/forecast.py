"""
ЖИВОЕ ПЕРЕОБУЧЕНИЕ — порт прогнозного пайплайна из nb03 поверх MultiModelZI.

Воспроизводит логику диплома (БЕЗ изменений):
  walk-forward CV (expanding window, с CV_START_YEAR) по 5 моделям → выбор top-3 по
  медианному ROC-AUC → изотоническая калибровка каждой модели на её OOF →
  ансамбль = среднее калиброванных вероятностей (p_ens) и среднее dps (dps_ens) →
  conformal-интервалы из OOF-остатков лучшей модели → stability = 0.6·p_cal + 0.4·streak_norm.

Отличие от build_artifact (который читает замороженный xlsx ВКР): здесь модель РЕАЛЬНО
обучается на текущей панели. Гейт корректности: на той же панели p_ens должен совпасть
с замороженным forecast_rf.json в пределах допуска (см. scripts/build_artifact.py --live).

ВНИМАНИЕ: walk-forward по 5 моделям тяжёлый (CatBoost/XGB/LGB). Для квартального
воркфлоу это нормально (минуты), но не для интерактивного прогона.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score

from .config import SEED, CV_START_YEAR, YIELD_CAP  # noqa: F401
from .pipeline import (
    MultiModelZI, safe_cat, safe_features, drop_correlated,
)

MODEL_NAMES = ("catboost", "xgboost", "lightgbm", "rf", "logreg")
CI_ALPHA = 0.10   # conformal interval level (90%) — как в нб03


def _median_auc(aucs: List[float]) -> float:
    a = [x for x in aucs if np.isfinite(x)]
    return float(np.median(a)) if a else float("nan")


def walk_forward_oof(df, feats, cat_feats, model_name) -> Tuple[pd.DataFrame, float]:
    """Expanding-window walk-forward (нб03 walk_forward_cv): (oof_df, median ROC-AUC)."""
    max_yr = int(df["year"].max())
    cat_idx = [feats.index(c) for c in cat_feats if c in feats]
    oof, aucs = [], []
    for vy in range(CV_START_YEAR, max_yr):
        trn = df[(df["year"] <= vy - 1) & (df["label_known_paid"] == 1)].copy()
        val = df[(df["year"] == vy) & (df["label_known_paid"] == 1)].copy()
        if len(trn) < 50 or len(val) < 5:
            continue
        for c in cat_feats:
            trn[c] = safe_cat(trn[c]); val[c] = safe_cat(val[c])
        m = MultiModelZI(model_name, seed=SEED)
        m.fit(trn[feats], trn["paid_next"].astype(int).values,
              trn["dps_next"].fillna(0).values, cat_feats=cat_feats, cat_idx=cat_idx)
        p, dps = m.predict(val[feats], cat_feats=cat_feats, cat_idx=cat_idx)
        paid_va = val["paid_next"].astype(int).values
        aucs.append(roc_auc_score(paid_va, p) if len(np.unique(paid_va)) > 1 else np.nan)
        oof.append(pd.DataFrame({
            "ticker": val["ticker"].values, "year": vy, "paid_next": paid_va,
            "p_hat": p, "dps_hat": dps, "dps_next": val["dps_next"].values}))
    oof_df = pd.concat(oof, ignore_index=True) if oof else pd.DataFrame()
    return oof_df, _median_auc(aucs)


def fit_conformal(oof: pd.DataFrame, alpha: float = CI_ALPHA) -> Tuple[float, float]:
    """Conformal-границы из OOF-остатков плательщиков (нб03 fit_conformal)."""
    paid = oof[oof["paid_next"] == 1]
    res = (paid["dps_next"] - paid["dps_hat"]).dropna()
    if len(res) < 10:
        return 0.0, 0.0
    return (float(np.percentile(res, alpha / 2 * 100)),
            float(np.percentile(res, (1 - alpha / 2) * 100)))


def _train_predict_latest(df, feats, cat_feats, model_name, pred_year):
    """Обучение на всех размеченных, прогноз для среза pred_year (нб03 train_final_and_predict)."""
    cat_idx = [feats.index(c) for c in cat_feats if c in feats]
    lab = df[df["label_known_paid"] == 1].copy()
    for c in cat_feats:
        lab[c] = safe_cat(lab[c])
    m = MultiModelZI(model_name, seed=SEED)
    m.fit(lab[feats], lab["paid_next"].astype(int).values,
          lab["dps_next"].fillna(0).values, cat_feats=cat_feats, cat_idx=cat_idx)
    latest = df[df["year"] == pred_year].copy()
    if len(latest) == 0:
        return None, None, None
    for c in cat_feats:
        if c in latest.columns:
            latest[c] = safe_cat(latest[c])
    p, dps = m.predict(latest[feats], cat_feats=cat_feats, cat_idx=cat_idx)
    return p, dps, latest


def train_and_forecast(df: pd.DataFrame, dps_col: str, flag_col: str,
                       cat_cols: List[str], pred_year: Optional[int] = None,
                       models=MODEL_NAMES) -> pd.DataFrame:
    """
    Полный живой прогноз. df — панель ПОСЛЕ build_targets/add_dsi_proxy.
    Возвращает DataFrame со схемой Russia_Full (как читает build_artifact):
      ticker, sector, p_ens, dps_ens, dps_lo_conf, dps_hi_conf, stability,
      div_streak, current_dps, current_paid. Метаданные — в .attrs.
    """
    feats, cats = safe_features(df)
    feats = drop_correlated(df, feats, cats)
    cat_feats = [c for c in cats if c in feats and c in cat_cols]
    if pred_year is None:
        pred_year = int(df["year"].max())

    # 1) walk-forward OOF по моделям → медианный AUC
    oofs: Dict[str, pd.DataFrame] = {}
    aucs: Dict[str, float] = {}
    for mn in models:
        oof, med = walk_forward_oof(df, feats, cat_feats, mn)
        if len(oof):
            oofs[mn] = oof
            aucs[mn] = med
    if not aucs:
        raise RuntimeError("walk-forward не дал OOF ни по одной модели")
    top3 = sorted(aucs, key=aucs.get, reverse=True)[:3]
    best = top3[0]

    # 2) ансамбль: каждая top-3 модель калибруется СВОИМ изотоном на своём OOF, усредняем
    ens_p = ens_dps = None
    latest_ref = None
    used = []
    for mn in top3:
        p, dps, latest = _train_predict_latest(df, feats, cat_feats, mn, pred_year)
        if p is None:
            continue
        iso = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip")
        iso.fit(oofs[mn]["p_hat"].values, oofs[mn]["paid_next"].values)
        p_cal = iso.predict(p)
        ens_p = p_cal / len(top3) if ens_p is None else ens_p + p_cal / len(top3)
        ens_dps = dps / len(top3) if ens_dps is None else ens_dps + dps / len(top3)
        latest_ref = latest
        used.append(mn)
    if latest_ref is None:
        raise RuntimeError(f"нет среза для pred_year={pred_year}")

    # 3) conformal-интервалы из OOF лучшей модели
    q_lo, q_hi = fit_conformal(oofs[best])

    # 4) stability и сборка рейтинга (нб03 build_rating)
    gmax = float(df["div_streak"].max()) if "div_streak" in df.columns else 1.0
    streak = (latest_ref["div_streak"].fillna(0).values
              if "div_streak" in latest_ref.columns else np.zeros(len(latest_ref)))
    stability = 0.6 * ens_p + 0.4 * (streak / max(gmax, 1.0))

    out = pd.DataFrame({
        "ticker": latest_ref["ticker"].values,
        "sector": (latest_ref["sector"].values if "sector" in latest_ref.columns else None),
        "p_ens": np.round(ens_p, 4),
        "dps_ens": np.round(ens_dps, 4),
        "dps_lo_conf": np.round(np.maximum(0, ens_dps + q_lo), 4),
        "dps_hi_conf": np.round(np.maximum(0, ens_dps + q_hi), 4),
        "stability": np.round(stability, 4),
        "div_streak": (latest_ref["div_streak"].values if "div_streak" in latest_ref.columns else None),
        "current_dps": (latest_ref[dps_col].values if dps_col in latest_ref.columns else None),
        "current_paid": (latest_ref[flag_col].values if flag_col in latest_ref.columns else None),
    })
    out.attrs["top3"] = used
    out.attrs["auc"] = {k: round(v, 4) for k, v in aucs.items()}
    out.attrs["best"] = best
    out.attrs["pred_year"] = pred_year
    out.attrs["conformal"] = (round(q_lo, 3), round(q_hi, 3))
    return out
