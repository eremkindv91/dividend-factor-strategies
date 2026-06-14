"""
Перенос функций из notebooks/03_ml_dividend_forecast.ipynb (без изменения логики):
загрузка панели, построение таргетов, DSI-прокси, отбор признаков, класс MultiModelZI.

Плюс одна добавленная обёртка `per_ticker_shap()` — она собирает SHAP в разрезе тикеров
для прогнозного среза (в ноутбуке SHAP считается только агрегированно). Сам расчёт SHAP
повторяет `compute_shap()`/`train_final_and_predict()` из ноутбука.
"""
from __future__ import annotations

import os
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.ensemble import (
    RandomForestClassifier,
    RandomForestRegressor,
)

from .config import (
    SEED,
    XGB_ITER, XGB_LR, XGB_DEPTH,
    EXCLUDE_ALWAYS, EXCLUDE_PREFIXES,
)


# ── helpers (нб 03) ───────────────────────────────────────────────────────
def safe_cat(series: pd.Series, fill: str = "Unknown") -> pd.Series:
    """Чистая строковая категория: NaN/'nan'/'None'/'' → fill."""
    return (series.fillna(fill).astype(str)
            .replace({"nan": fill, "None": fill, "": fill}))


def load_panel(path: str) -> pd.DataFrame:
    """Загрузка панели (.csv или .xlsx), очистка колонок, дедупликация ticker-year."""
    assert os.path.exists(path), f"File not found: {path}"
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path, engine="openpyxl")
    df.columns = [c.strip() for c in df.columns]
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].astype(str).str.strip()
    if "year" in df.columns:
        df["year"] = df["year"].astype(int)
    df = df.drop_duplicates(subset=["ticker", "year"], keep="first")
    return df


def build_targets(df: pd.DataFrame, dps_col: str, flag_col: str,
                  price_col: str) -> pd.DataFrame:
    """Forward-shifted таргеты paid_next / dps_next / y_next_pct (нб 03)."""
    df_s = df.sort_values(["ticker", "year"]).copy()
    df_s["paid_next"] = df_s.groupby("ticker")[flag_col].shift(-1)
    df_s["dps_next"] = df_s.groupby("ticker")[dps_col].shift(-1)
    df_s["_next_yr"] = df_s.groupby("ticker")["year"].shift(-1)

    ok = df_s["_next_yr"] == df_s["year"] + 1
    df_s.loc[~ok, ["paid_next", "dps_next"]] = np.nan
    df_s["label_known_paid"] = np.where(ok & df_s["paid_next"].notna(), 1, 0)

    price_ok = df_s[price_col].notna() & (df_s[price_col] > 0)
    df_s["label_known_yield"] = np.where(
        (df_s["label_known_paid"] == 1) & price_ok, 1, 0)
    df_s["y_next_pct"] = np.where(
        df_s["label_known_yield"] == 1,
        100.0 * df_s["dps_next"].fillna(0) / df_s[price_col],
        np.nan)

    df_s.drop(columns=["_next_yr"], inplace=True)
    return df_s


def add_dsi_proxy(df: pd.DataFrame) -> pd.DataFrame:
    """DSI = (min(streak,7)/7 + min(growth_streak,7)/7)/2 (нб 03, порог 7 — Наимов 2019)."""
    df = df.copy()
    df["dsi_proxy"] = (
        df["div_streak"].clip(0, 7).fillna(0) / 7
        + df["div_growth_streak"].clip(0, 7).fillna(0) / 7
    ) / 2
    return df


def safe_features(df: pd.DataFrame) -> Tuple[List[str], List[str]]:
    """Отбор признаков: (all_features, categorical_features). Дословно из нб 03."""
    exclude = set(EXCLUDE_ALWAYS)
    feats, cats = [], []
    for c in df.columns:
        if c in exclude or any(c.startswith(p) for p in EXCLUDE_PREFIXES):
            continue
        if df[c].dtype == object or isinstance(df[c].dtype, pd.CategoricalDtype):
            nu = df[c].nunique()
            if 1 < nu < 100:
                feats.append(c)
                cats.append(c)
            continue
        if not np.issubdtype(df[c].dtype, np.number):
            continue
        non_na = df[c].dropna()
        if len(non_na) < len(df) * 0.02:
            continue
        if non_na.std() < 1e-12:
            continue
        feats.append(c)
    return feats, cats


def drop_correlated(df: pd.DataFrame, feats: List[str],
                    cat_feats: List[str], threshold: float = 0.98) -> List[str]:
    """Удалить по одному из пары признаков с |r|>threshold (нб 03)."""
    num = [f for f in feats if f not in cat_feats]
    if len(num) < 2:
        return feats
    corr = df[num].corr().abs()
    upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
    to_drop = {col for col in upper.columns if any(upper[col] > threshold)}
    return [f for f in feats if f not in to_drop]


# ── MultiModelZI (нб 03) ──────────────────────────────────────────────────
class MultiModelZI:
    """Двухэтапная zero-inflated модель: классификатор + регрессор на плательщиках.
    Перенос из ноутбука. Импорты catboost/lightgbm ленивые — для xgboost не нужны."""
    SUPPORTED = ("catboost", "xgboost", "lightgbm", "rf", "logreg")

    def __init__(self, model_name: str, seed: int = SEED):
        assert model_name in self.SUPPORTED, f"Unknown model: {model_name}"
        self.name = model_name
        self.seed = seed
        self.clf = None
        self.reg = None
        self._le: Dict[str, LabelEncoder] = {}
        self._lgbm_cats: Dict[str, list] = {}
        self._fill_med = None
        self._scaler = None
        self._median_dps = 0.0

    def _encode_train(self, X_tr, X_va, cat_feats):
        X_tr, X_va = X_tr.copy(), (X_va.copy() if X_va is not None else None)
        for c in cat_feats:
            le = LabelEncoder()
            X_tr[c] = le.fit_transform(X_tr[c].astype(str))
            self._le[c] = le
            if X_va is not None:
                mp = {v: i for i, v in enumerate(le.classes_)}
                X_va[c] = X_va[c].astype(str).map(mp).fillna(-1).astype(int)
        return X_tr, X_va

    def _encode_pred(self, X, cat_feats):
        X = X.copy()
        for c in cat_feats:
            if c in self._le:
                mp = {v: i for i, v in enumerate(self._le[c].classes_)}
                X[c] = X[c].astype(str).map(mp).fillna(-1).astype(int)
        return X

    def _fill_numeric(self, X_tr, X_va=None):
        self._fill_med = X_tr.select_dtypes(include=[np.number]).median().fillna(0)
        X_tr = X_tr.copy()
        X_va = X_va.copy() if X_va is not None else None
        for c in self._fill_med.index:
            if c in X_tr.columns:
                X_tr[c] = X_tr[c].fillna(self._fill_med[c])
        if X_va is not None:
            for c in self._fill_med.index:
                if c in X_va.columns:
                    X_va[c] = X_va[c].fillna(self._fill_med[c])
        return X_tr, X_va

    def _fill_pred(self, X):
        if self._fill_med is None:
            return X
        X = X.copy()
        for c in self._fill_med.index:
            if c in X.columns:
                X[c] = X[c].fillna(self._fill_med[c])
        return X

    def _preprocess_train(self, X_tr, X_va, cat_feats):
        nm = self.name
        if nm == "catboost":
            return X_tr.copy(), (X_va.copy() if X_va is not None else None)
        elif nm == "lightgbm":
            X_tr, X_va = X_tr.copy(), (X_va.copy() if X_va is not None else None)
            for c in cat_feats:
                cats = sorted(X_tr[c].astype(str).unique())
                self._lgbm_cats[c] = cats
                X_tr[c] = pd.Categorical(X_tr[c].astype(str), categories=cats)
                if X_va is not None:
                    X_va[c] = pd.Categorical(X_va[c].astype(str), categories=cats)
            return X_tr, X_va
        elif nm == "logreg":
            X_tr, X_va = self._encode_train(X_tr, X_va, cat_feats)
            X_tr, X_va = self._fill_numeric(X_tr, X_va)
            self._scaler = StandardScaler()
            cols = X_tr.columns
            X_tr = pd.DataFrame(self._scaler.fit_transform(X_tr),
                                columns=cols, index=X_tr.index)
            if X_va is not None:
                X_va = pd.DataFrame(self._scaler.transform(X_va),
                                    columns=cols, index=X_va.index)
            return X_tr, X_va
        else:  # xgboost, rf
            X_tr, X_va = self._encode_train(X_tr, X_va, cat_feats)
            X_tr, X_va = self._fill_numeric(X_tr, X_va)
            return X_tr, X_va

    def _preprocess_pred(self, X, cat_feats):
        nm = self.name
        if nm == "catboost":
            return X.copy()
        elif nm == "lightgbm":
            X = X.copy()
            for c in cat_feats:
                cats = self._lgbm_cats.get(c, sorted(X[c].astype(str).unique()))
                X[c] = pd.Categorical(X[c].astype(str), categories=cats)
            return X
        elif nm == "logreg":
            Xp = self._fill_pred(self._encode_pred(X, cat_feats))
            cols = Xp.columns
            return pd.DataFrame(self._scaler.transform(Xp),
                                columns=cols, index=Xp.index)
        else:
            return self._fill_pred(self._encode_pred(X, cat_feats))

    def fit(self, X_all, paid, dps, cat_feats=None, cat_idx=None,
            X_val=None, paid_val=None, dps_val=None):
        cat_feats = cat_feats or []
        cat_idx = cat_idx or []
        nm = self.name
        self._median_dps = float(np.median(dps[paid == 1])) if (paid == 1).sum() else 0.0
        X_tr, X_va = self._preprocess_train(X_all, X_val, cat_feats)

        pos = max(paid.sum(), 1)
        neg = max(len(paid) - pos, 1)
        spw = neg / pos

        if nm == "catboost":
            from catboost import CatBoostClassifier
            from .config import SEED as _S  # noqa
            self.clf = CatBoostClassifier(
                iterations=1200, learning_rate=0.04, depth=6, l2_leaf_reg=3.0,
                auto_class_weights="Balanced", cat_features=cat_idx,
                random_seed=self.seed, verbose=0, nan_mode="Min")
            self.clf.fit(X_tr, paid)
        elif nm == "xgboost":
            from xgboost import XGBClassifier
            self.clf = XGBClassifier(
                n_estimators=XGB_ITER, learning_rate=XGB_LR, max_depth=XGB_DEPTH,
                scale_pos_weight=spw, subsample=0.8, colsample_bytree=0.8,
                random_state=self.seed, verbosity=0, eval_metric="logloss")
            self.clf.fit(X_tr, paid)
        elif nm == "lightgbm":
            from lightgbm import LGBMClassifier
            self.clf = LGBMClassifier(
                n_estimators=600, learning_rate=0.04, max_depth=6,
                is_unbalance=True, subsample=0.8, colsample_bytree=0.8,
                random_state=self.seed, verbosity=-1)
            self.clf.fit(X_tr, paid)
        elif nm == "rf":
            self.clf = RandomForestClassifier(
                n_estimators=400, max_depth=10, class_weight="balanced",
                random_state=self.seed, n_jobs=1)
            self.clf.fit(X_tr, paid)
        else:  # logreg
            self.clf = LogisticRegression(
                C=1.0, max_iter=2000, class_weight="balanced",
                random_state=self.seed)
            self.clf.fit(X_tr, paid)

        # ── регрессор только на плательщиках ──
        mask = paid == 1
        if mask.sum() < 10:
            self.reg = None
            return self
        y_reg = np.log1p(dps[mask])
        X_reg = X_tr.iloc[np.where(mask)[0]]
        if nm == "catboost":
            from catboost import CatBoostRegressor
            self.reg = CatBoostRegressor(
                iterations=1200, learning_rate=0.04, depth=6, l2_leaf_reg=3.0,
                cat_features=cat_idx, random_seed=self.seed, verbose=0, nan_mode="Min")
            self.reg.fit(X_reg, y_reg)
        elif nm == "xgboost":
            from xgboost import XGBRegressor
            self.reg = XGBRegressor(
                n_estimators=XGB_ITER, learning_rate=XGB_LR, max_depth=XGB_DEPTH,
                subsample=0.8, colsample_bytree=0.8, random_state=self.seed, verbosity=0)
            self.reg.fit(X_reg, y_reg)
        elif nm == "lightgbm":
            from lightgbm import LGBMRegressor
            self.reg = LGBMRegressor(
                n_estimators=600, learning_rate=0.04, max_depth=6,
                subsample=0.8, colsample_bytree=0.8, random_state=self.seed, verbosity=-1)
            self.reg.fit(X_reg, y_reg)
        elif nm == "rf":
            self.reg = RandomForestRegressor(
                n_estimators=400, max_depth=10, random_state=self.seed, n_jobs=1)
            self.reg.fit(X_reg, y_reg)
        else:
            self.reg = Ridge(alpha=1.0)
            self.reg.fit(X_reg, y_reg)
        return self

    def predict(self, X, cat_feats=None, cat_idx=None):
        cat_feats = cat_feats or []
        Xp = self._preprocess_pred(X, cat_feats)
        p_hat = self.clf.predict_proba(Xp)[:, 1]
        if self.reg is not None:
            dps_hat = np.maximum(np.expm1(self.reg.predict(Xp)), 0)
        else:
            dps_hat = np.full(len(X), self._median_dps)
        return p_hat, dps_hat


# ── per-ticker SHAP (обёртка над compute_shap/ train_final_and_predict из нб 03) ──
def per_ticker_shap(df: pd.DataFrame, feats: List[str], cat_feats: List[str],
                    model_name: str, pred_year: int, top_k: int = 5):
    """Обучить лучшую модель на размеченных данных и вернуть SHAP топ-k по каждому
    тикеру прогнозного среза year==pred_year. Возвращает (dict ticker→[(feat, impact)],
    обученная модель, auc_in_sample). TreeExplainer — только для дерев. моделей."""
    import shap
    from sklearn.metrics import roc_auc_score

    labeled = df[df["label_known_paid"] == 1].copy()
    for c in cat_feats:
        labeled[c] = safe_cat(labeled[c])
    cat_idx = [feats.index(c) for c in cat_feats if c in feats]

    model = MultiModelZI(model_name, seed=SEED)
    model.fit(labeled[feats], labeled["paid_next"].astype(int).values,
              labeled["dps_next"].fillna(0).values,
              cat_feats=cat_feats, cat_idx=cat_idx)

    # in-sample AUC как sanity (должен быть ~0.9+, подтверждает верный перенос)
    p_in, _ = model.predict(labeled[feats], cat_feats=cat_feats, cat_idx=cat_idx)
    try:
        auc = float(roc_auc_score(labeled["paid_next"].astype(int).values, p_in))
    except Exception:
        auc = float("nan")

    latest = df[df["year"] == pred_year].copy()
    for c in cat_feats:
        if c in latest.columns:
            latest[c] = safe_cat(latest[c])
    X_lat = model._preprocess_pred(latest[feats], cat_feats)

    explainer = shap.TreeExplainer(model.clf)
    sv = explainer.shap_values(X_lat)
    if isinstance(sv, list):
        sv = sv[1]  # класс=1 (выплата)
    sv = np.asarray(sv)

    out: Dict[str, list] = {}
    tickers = latest["ticker"].values
    for i, tk in enumerate(tickers):
        row = sv[i]
        order = np.argsort(np.abs(row))[::-1][:top_k]
        out[str(tk)] = [(feats[j], float(row[j])) for j in order]
    return out, model, auc


def ensemble_shap(df: pd.DataFrame, feats: List[str], cat_feats: List[str],
                  model_names: List[str], pred_year: int, top_k: int = 5):
    """SHAP в разрезе тикеров, УСРЕДНЁННЫЙ по моделям ансамбля (top-3) — отражает
    драйверы ансамблевого прогноза, а не одной модели. Каждая модель обучается на
    размеченных данных, TreeExplainer считается на прогнозном срезе year==pred_year.

    Единицы SHAP различаются между моделями (бустинги — лог-оддсы, RF — вероятность),
    поэтому вклад каждой модели нормируется построчно на сумму |вклада| и затем
    усредняется. Возвращает (dict ticker→[(feat, impact)], число использованных моделей)."""
    import shap

    labeled = df[df["label_known_paid"] == 1].copy()
    for c in cat_feats:
        labeled[c] = safe_cat(labeled[c])
    cat_idx = [feats.index(c) for c in cat_feats if c in feats]

    latest = df[df["year"] == pred_year].copy()
    for c in cat_feats:
        if c in latest.columns:
            latest[c] = safe_cat(latest[c])
    tickers = latest["ticker"].values
    n, F = len(latest), len(feats)
    acc = np.zeros((n, F))
    used = 0

    for mn in model_names:
        model = MultiModelZI(mn, seed=SEED)
        model.fit(labeled[feats], labeled["paid_next"].astype(int).values,
                  labeled["dps_next"].fillna(0).values,
                  cat_feats=cat_feats, cat_idx=cat_idx)
        X = model._preprocess_pred(latest[feats], cat_feats)
        try:
            sv = shap.TreeExplainer(model.clf).shap_values(X)
        except Exception:
            continue
        if isinstance(sv, list):           # старый формат: список по классам
            sv = sv[1]
        sv = np.asarray(sv, dtype=float)
        if sv.ndim == 3:                   # новый формат: (n, F, n_classes)
            sv = sv[:, :, 1]
        denom = np.abs(sv).sum(axis=1, keepdims=True)
        denom[denom == 0] = 1.0
        acc += sv / denom                  # построчная нормировка → сопоставимость моделей
        used += 1

    if used:
        acc /= used
    out: Dict[str, list] = {}
    for i, tk in enumerate(tickers):
        row = acc[i]
        order = np.argsort(np.abs(row))[::-1][:top_k]
        out[str(tk)] = [(feats[j], float(row[j])) for j in order]
    return out, used
