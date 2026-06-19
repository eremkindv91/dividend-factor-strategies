#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TRAIN-этап (ручной, workflow_dispatch). Собирает ЗАМОРОЖЕННЫЙ артефакт прогноза
model_output/forecast_rf.json из результатов ВКР:

  • прогнозные величины (p_ens, dps_ens, интервалы, stability) берутся КАК ЕСТЬ из
    results/ml_forecast_v5/dividend_forecast_2026_v5.xlsx (лист Russia_Full) — не пересчитываем,
    чтобы не разойтись с числами диплома;
  • per-ticker SHAP (топ-5) считается заново через divmodel (логика нб 03), т.к. в ноутбуке
    SHAP сохранён только агрегированно. SHAP ИЛЛЮСТРАТИВЕН — по лучшей одиночной модели
    (xgboost), а не декомпозиция калиброванного ансамбля;
  • payout — последний известный факт payout_ratio_pct из панели (помечается как факт, не прогноз).

Цены и дивдоходность в артефакт НЕ кладутся — их считает инференс build_data.py к свежей цене.

Запуск:  python scripts/build_artifact.py
"""
from __future__ import annotations

import json
import os
import sys
import warnings
from datetime import datetime, timezone

warnings.simplefilter("ignore")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts"))

import numpy as np
import pandas as pd

import divmodel as dm
from divmodel.config import (
    RF_DPS_COL, RF_FLAG_COL, RF_PRICE_COL, RF_CAT_COLS, BEST_MODEL_RF,
)
from feature_labels import get_label, direction_ru

ARTIFACT_XLSX = os.path.join(REPO, "results", "ml_forecast_v5", "dividend_forecast_2026_v5.xlsx")
PANEL_CSV = os.path.join(REPO, "data", "panels_final", "panel_russia_final.csv")
OUT_JSON = os.path.join(REPO, "model_output", "forecast_rf.json")

AUC_OOF_RF = 0.904  # walk-forward OOF AUC из results_snapshot.json (для подписи на сайте)


def _round(x, n):
    try:
        v = float(x)
        return round(v, n) if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def forecast_asof() -> str:
    """Дата прогона ВКР: дата изменения файла-артефакта (YYYY-MM-DD)."""
    ts = os.path.getmtime(ARTIFACT_XLSX)
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")


def top3_ensemble_models() -> list:
    """Top-3 модели ансамбля РФ по медианному walk-forward AUC из сохранённых OOF
    (как выбирается ансамбль в нб 03). Ограничиваем tree-объяснимыми (cb/xgb/lgb/rf),
    т.к. SHAP считается TreeExplainer'ом."""
    import glob
    from sklearn.metrics import roc_auc_score
    rows = []
    for f in glob.glob(os.path.join(REPO, "results", "ml_forecast_v5", "oof_rf_*.csv")):
        mn = os.path.basename(f).replace("oof_rf_", "").replace(".csv", "")
        d = pd.read_csv(f)
        aucs = [roc_auc_score(g["paid_next"], g["p_hat"])
                for _, g in d.groupby("year") if g["paid_next"].nunique() > 1]
        if aucs:
            rows.append((mn, float(pd.Series(aucs).median())))
    rows.sort(key=lambda x: -x[1])
    tree = [m for m, _ in rows if m in ("catboost", "xgboost", "lightgbm", "rf")]
    return tree[:3] if tree else [BEST_MODEL_RF]


def main() -> int:
    live = "--live" in sys.argv

    # ── панель → признаки (нужны и для SHAP, и для live-переобучения) ──
    print("[build_artifact] панель и признаки:", os.path.relpath(PANEL_CSV, REPO))
    panel = dm.load_panel(PANEL_CSV)
    panel = dm.build_targets(panel, RF_DPS_COL, RF_FLAG_COL, RF_PRICE_COL)
    panel = dm.add_dsi_proxy(panel)
    feats, cats = dm.safe_features(panel)
    feats = dm.drop_correlated(panel, feats, cats)
    cat_feats = [c for c in cats if c in feats and c in RF_CAT_COLS]
    pred_year = int(panel["year"].max())
    print(f"  признаков: {len(feats)} | прогнозный срез year={pred_year} → дивиденды {pred_year + 1}")

    # ── источник прогноза: LIVE-переобучение ИЛИ замороженный xlsx ВКР ──
    if live:
        from divmodel.forecast import train_and_forecast
        print("[build_artifact] LIVE: переобучение ансамбля на текущей панели (тяжело, минуты)…")
        fc = train_and_forecast(panel, RF_DPS_COL, RF_FLAG_COL, RF_CAT_COLS, pred_year=pred_year)
        auc_used = fc.attrs.get("auc", {}).get(fc.attrs.get("best"), AUC_OOF_RF)
        # сан-гейты (fail-safe: не публикуем мусор)
        assert len(fc) >= 200, f"live: мало тикеров ({len(fc)})"
        assert fc["p_ens"].between(0, 1).all(), "live: p_ens вне [0,1]"
        assert auc_used >= 0.80, f"live: AUC подозрительно низкий ({auc_used:.3f})"
        top3 = fc.attrs["top3"]
        print(f"  LIVE: {len(fc)} тикеров | top3={top3} | AUC*={auc_used:.3f} | сан-гейты OK")
    else:
        print("[build_artifact] загрузка прогноза ВКР:", os.path.relpath(ARTIFACT_XLSX, REPO))
        fc = pd.read_excel(ARTIFACT_XLSX, sheet_name="Russia_Full")
        auc_used = AUC_OOF_RF
        top3 = top3_ensemble_models()
    fc["ticker"] = fc["ticker"].astype(str).str.strip()
    print(f"  прогноз: {len(fc)} тикеров")

    print(f"[build_artifact] ансамблевый SHAP по top-3 моделям {top3}...")
    shap_map, n_models = dm.ensemble_shap(panel, feats, cat_feats, top3, pred_year, top_k=5)
    print(f"  SHAP усреднён по {n_models} моделям ансамбля; {len(shap_map)} тикеров")

    # ── последний фактический payout по тикеру (факт, не прогноз) ──
    # Берём последний год, где payout_ratio_pct реально есть (>0). Пересчёт из панели НЕ делаем:
    # он лез в прогнозный год и давал частичные/искажённые значения (GCHE, TATNP). Устаревший
    # payout (год < STALE_CUTOFF) скрываем при сборке («—»), чтобы древний показатель (напр.
    # Норникель 2013) не читался как актуальный.
    STALE_CUTOFF = pred_year - 1  # 2024: показываем payout только за последний закрытый год и новее
    pay = panel[panel["payout_ratio_pct"].notna()
                & (panel["payout_ratio_pct"] > 0)].sort_values("year")
    payout_last = (pay.groupby("ticker")
                   .agg(payout_last=("payout_ratio_pct", "last"),
                        payout_last_year=("year", "last")))

    panel_tickers = set(panel.loc[panel["year"] == pred_year, "ticker"])
    missing_shap = sorted(set(fc["ticker"]) - set(shap_map))
    if missing_shap:
        print(f"  ⚠ нет SHAP для {len(missing_shap)} тикеров (нет строки в срезе {pred_year}): "
              f"{missing_shap[:8]}{'...' if len(missing_shap) > 8 else ''}")

    # ── сборка записей ──
    records = []
    for _, r in fc.iterrows():
        tk = r["ticker"]
        p_ens = _round(r.get("p_ens"), 4)
        shap5 = []
        for feat, impact in shap_map.get(tk, []):
            shap5.append({
                "feature": feat,
                "feature_ru": get_label(feat),
                "impact": _round(impact, 4),
                "direction": direction_ru(impact),
            })
        rec = {
            "ticker": tk,
            "sector": (None if pd.isna(r.get("sector")) else str(r.get("sector"))),
            "p_ens": p_ens,
            "cut_risk": (None if p_ens is None else _round(1.0 - p_ens, 4)),
            "stability_score": _round(r.get("stability"), 4),
            "dividend_forecast": _round(r.get("dps_ens"), 4),
            "dividend_forecast_lo": _round(r.get("dps_lo_conf"), 4),
            "dividend_forecast_hi": _round(r.get("dps_hi_conf"), 4),
            "div_streak": (int(r["div_streak"]) if pd.notna(r.get("div_streak")) else None),
            "current_dps": _round(r.get("current_dps"), 4),
            "current_paid": (int(r["current_paid"]) if pd.notna(r.get("current_paid")) else None),
            "shap_top5": shap5,
        }
        if tk in payout_last.index and int(payout_last.loc[tk, "payout_last_year"]) >= STALE_CUTOFF:
            rec["payout_last"] = _round(payout_last.loc[tk, "payout_last"], 2)
            rec["payout_last_year"] = int(payout_last.loc[tk, "payout_last_year"])
        else:
            rec["payout_last"] = None        # нет актуального payout (или только устаревший) → «—»
            rec["payout_last_year"] = None
        records.append(rec)

    # ── Пост-обработка по УТВЕРЖДЁННОМУ аудиту (правило, НЕ точечная правка артефакта) ──
    # Пары обычка/преф по тикеру (база+P); в аудите все 38 подтверждены по имени (поле name).
    by_tk = {r["ticker"]: r for r in records}
    DUAL_RATIO_THR = 1.5      # порог материальной дивергенции DPS внутри пары (медиана пар = 1.43)
    DUAL_EXCEPT = {"WTCMP"}   # панель префа БОГАЧЕ обычки и дивергенция пограничная (1.50×) — оставляем
    AUDIT2_FLAG = {"OMZZP"}   # стендэлон-преф: неправдоподобное возобновление (≈168₽ vs ≈0₽) из разреж. панели

    # 1) payout эмитента → префу: payout_ratio — показатель уровня КОМПАНИИ, одинаков для обоих
    #    классов акций. Это не «копирование DPS» (DPS — на акцию), а тот же факт эмитента. Кроме стендэлонов.
    n_inherited = 0
    for tk, r in by_tk.items():
        if tk.endswith("P") and tk[:-1] in by_tk:
            base = by_tk[tk[:-1]]
            if base.get("payout_last") is not None:
                r["payout_last"] = base["payout_last"]
                r["payout_last_year"] = base["payout_last_year"]
                r["payout_source"] = tk[:-1]   # факт из отчётности обычки (тот же эмитент)
                n_inherited += 1

    # 2) пометка ненадёжного DPS (cut_risk/stability/ p_ens СОХРАНЯЮТСЯ; число DPS не фабрикуем)
    flagged = {}
    for tk, r in by_tk.items():
        if tk.endswith("P") and tk[:-1] in by_tk and tk not in DUAL_EXCEPT:
            do = by_tk[tk[:-1]].get("dividend_forecast"); dp = r.get("dividend_forecast")
            if isinstance(do, (int, float)) and isinstance(dp, (int, float)) and min(do, dp) > 0:
                ratio = max(do, dp) / min(do, dp)
                if ratio >= DUAL_RATIO_THR:
                    flagged[tk] = (f"dual-class: DPS расходится с {tk[:-1]} в {ratio:.2f}× "
                                   f"(строка префа разрежена) — число ненадёжно")
    for tk in AUDIT2_FLAG:
        if tk in by_tk:
            flagged[tk] = "разреженная панель: прогноз неправдоподобен относительно истории — число ненадёжно"

    for tk, reason in flagged.items():
        r = by_tk[tk]
        r["dividend_forecast"] = None
        r["dividend_forecast_lo"] = None
        r["dividend_forecast_hi"] = None
        r["forecast_status"] = "insufficient_data"
        r["forecast_note"] = reason

    artifact = {
        "meta": {
            "market": "RU",
            "forecast_asof": (datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
                              if live else forecast_asof()),
            "feature_year": pred_year,
            "forecast_year": pred_year + 1,
            "n_tickers": len(records),
            "model": ("Stage1 — калиброванный ансамбль top-3 (CatBoost/XGBoost/LightGBM, "
                      "изотоническая калибровка); Stage2 — регрессия размера дивиденда. "
                      + ("Переобучено на текущей панели (live)." if live
                         else "Прогнозы взяты из прогона ВКР.")),
            "auc_oof_rf": round(auc_used, 4),
            "shap_models": top3,
            "shap_note": ("SHAP усреднён по моделям ансамбля (top-3: " + ", ".join(top3) + "), "
                          "вклад каждой модели нормирован построчно. Отражает направление и состав "
                          "факторов, влияющих на вероятность выплаты в ансамблевом прогнозе "
                          "(величины приближённые — калибровка вероятности монотонна)."),
            "shap_features_used": len(feats),
            "source_note": ("Прогноз переобучен на текущей панели (live) — обновляется квартально."
                            if live else
                            "Прогноз заморожен на дату прогона ВКР; обновляется только при ручной пересборке."),
            "audit": {
                "dual_class_ratio_threshold": DUAL_RATIO_THR,
                "dps_flagged": sorted(flagged),
                "n_dps_flagged": len(flagged),
                "n_payout_inherited": n_inherited,
                "note": ("Ненадёжный DPS помечен insufficient_data по dual-class (расхождение ≥1.5× "
                         "при разреженной строке префа) и аномалии к истории; cut_risk/stability "
                         "сохранены, число DPS не фабрикуется. payout префа наследует факт эмитента."),
            },
            "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        },
        "tickers": records,
    }

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(artifact, f, ensure_ascii=False, indent=2)

    n_shap = sum(1 for r in records if r["shap_top5"])
    n_payout = sum(1 for r in records if r["payout_last"] is not None)
    print(f"[build_artifact] записано: {os.path.relpath(OUT_JSON, REPO)}")
    print(f"  тикеров={len(records)} | с SHAP={n_shap} | с payout-фактом={n_payout} | "
          f"forecast_asof={artifact['meta']['forecast_asof']}")
    print(f"  DPS помечено insufficient_data: {len(flagged)} → {sorted(flagged)}")
    print(f"  payout унаследован префами от обычки: {n_inherited}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
