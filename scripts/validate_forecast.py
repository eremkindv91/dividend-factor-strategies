#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ГЕЙТ КОРРЕКТНОСТИ живого переобучения.

Запускает divmodel.forecast.train_and_forecast (все 5 моделей → top-3 ансамбль) на
текущей панели и сверяет p_ens с ЗАМОРОЖЕННЫМ прогнозом ВКР (model_output/forecast_rf.json).
Если перенос верен — на той же панели корреляция p_ens ≈0.99+ и малый MAE.

Запуск (тяжёлый, ~10 мин): python scripts/validate_forecast.py
"""
import json
import os
import sys
import time

import numpy as np  # noqa: F401
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import divmodel as dm  # noqa: E402
from divmodel.config import RF_DPS_COL, RF_FLAG_COL, RF_PRICE_COL, RF_CAT_COLS  # noqa: E402
from divmodel.forecast import train_and_forecast  # noqa: E402

PANEL = os.path.join(REPO, "data", "panels_final", "panel_russia_final.csv")
FROZEN = os.path.join(REPO, "model_output", "forecast_rf.json")
OUT = os.path.join(REPO, "results", "smartlab", "live_forecast.csv")


def main():
    t0 = time.time()
    panel = dm.load_panel(PANEL)
    panel = dm.build_targets(panel, RF_DPS_COL, RF_FLAG_COL, RF_PRICE_COL)
    panel = dm.add_dsi_proxy(panel)
    out = train_and_forecast(panel, RF_DPS_COL, RF_FLAG_COL, RF_CAT_COLS)
    print(f"[validate] {len(out)} тикеров | pred_year={out.attrs['pred_year']} | "
          f"top3={out.attrs['top3']} | время={time.time()-t0:.0f}s")
    print(f"[validate] AUC по моделям: {out.attrs['auc']}")
    print(f"[validate] p_ens: min={out.p_ens.min():.3f} mean={out.p_ens.mean():.3f} max={out.p_ens.max():.3f}")

    fr = json.load(open(FROZEN, encoding="utf-8"))
    fz = {r["ticker"]: r["p_ens"] for r in fr["tickers"] if r.get("p_ens") is not None}
    m = out[out.ticker.isin(fz)].copy()
    m["frozen"] = m.ticker.map(fz)
    corr = float(m.p_ens.corr(m.frozen))
    mae = float((m.p_ens - m.frozen).abs().mean())
    print(f"\n[validate] СВЕРКА с замороженным (общих {len(m)}): corr={corr:.4f}, MAE={mae:.4f}")
    gate = "✅ ПРОЙДЕН" if (corr >= 0.97 and mae <= 0.10) else "⚠️ ПРОВЕРИТЬ (порт мог разойтись)"
    print(f"[validate] гейт корректности: {gate} (порог corr≥0.97, MAE≤0.10)")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out.to_csv(OUT, index=False)
    print(f"[validate] живой прогноз сохранён: {os.path.relpath(OUT, REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
