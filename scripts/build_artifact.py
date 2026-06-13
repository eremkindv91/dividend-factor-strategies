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


def main() -> int:
    print("[build_artifact] загрузка прогноза ВКР:", os.path.relpath(ARTIFACT_XLSX, REPO))
    fc = pd.read_excel(ARTIFACT_XLSX, sheet_name="Russia_Full")
    fc["ticker"] = fc["ticker"].astype(str).str.strip()
    print(f"  Russia_Full: {len(fc)} тикеров")

    # ── панель → признаки → per-ticker SHAP (логика нб 03) ──
    print("[build_artifact] панель и признаки:", os.path.relpath(PANEL_CSV, REPO))
    panel = dm.load_panel(PANEL_CSV)
    panel = dm.build_targets(panel, RF_DPS_COL, RF_FLAG_COL, RF_PRICE_COL)
    panel = dm.add_dsi_proxy(panel)
    feats, cats = dm.safe_features(panel)
    feats = dm.drop_correlated(panel, feats, cats)
    cat_feats = [c for c in cats if c in feats and c in RF_CAT_COLS]
    pred_year = int(panel["year"].max())
    print(f"  признаков: {len(feats)} | прогнозный срез year={pred_year} → дивиденды {pred_year + 1}")

    print(f"[build_artifact] обучение {BEST_MODEL_RF} + SHAP (иллюстративно)...")
    shap_map, _model, auc_in = dm.per_ticker_shap(
        panel, feats, cat_feats, BEST_MODEL_RF, pred_year, top_k=5)
    print(f"  in-sample AUC={auc_in:.3f} (норма для бустинга); SHAP по {len(shap_map)} тикерам")

    # ── последний известный payout по тикеру (факт, не прогноз) ──
    # payout_ratio_pct последнего года часто =0 (дивиденд за прогнозный год ещё не разнесён),
    # поэтому берём последний год с РЕАЛЬНЫМ payout (>0).
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
        if tk in payout_last.index:
            rec["payout_last"] = _round(payout_last.loc[tk, "payout_last"], 2)
            rec["payout_last_year"] = int(payout_last.loc[tk, "payout_last_year"])
        else:
            rec["payout_last"] = None
            rec["payout_last_year"] = None
        records.append(rec)

    artifact = {
        "meta": {
            "market": "RU",
            "forecast_asof": forecast_asof(),
            "feature_year": pred_year,
            "forecast_year": pred_year + 1,
            "n_tickers": len(records),
            "model": ("Stage1 — калиброванный ансамбль top-3 (CatBoost/XGBoost/LightGBM, "
                      "изотоническая калибровка); Stage2 — регрессия размера дивиденда. "
                      "Прогнозы взяты из прогона ВКР."),
            "auc_oof_rf": AUC_OOF_RF,
            "shap_note": ("SHAP иллюстративен: рассчитан по лучшей одиночной модели (xgboost) "
                          "на признаках финальной панели; не является точной декомпозицией "
                          "ансамблевого прогноза."),
            "shap_features_used": len(feats),
            "source_note": "Прогноз заморожен на дату прогона ВКР; обновляется только при ручной пересборке.",
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
