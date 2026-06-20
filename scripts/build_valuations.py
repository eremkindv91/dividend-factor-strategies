#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Дополняет model_output/forecast_rf.json двумя блоками на каждый тикер:
  • valuation — роутер оценки (метод по классу: DCF/DDM/Multiples/SOTP/NAV/Special),
    fair_price, note/alert, допущения, матрица чувствительности;
  • history — ряды фундаментала по годам (выручка/прибыль/долг/EBITDA/ROE) для графиков.

Запуск в refresh.yml ПОСЛЕ build_artifact (квартально). Rf — live (ОФЗ); цены — из панели
(upside пересчитывает build_data к свежей дневной цене). Дивиденд D1 для DDM — из артефакта.

Запуск:  python scripts/build_valuations.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import datetime, timezone

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
from valuation_router import ValuationRouter, MoexMarket  # noqa: E402

ARTIFACT = os.path.join(REPO, "model_output", "forecast_rf.json")
PANEL = os.path.join(REPO, "data", "panels_final", "panel_russia_final.csv")

# поля истории для графиков (колонка панели → подпись на фронте)
HIST_FIELDS = {
    "revenue_mln": "Выручка",
    "net_profit_mln": "Чистая прибыль",
    "ebitda_mln": "EBITDA",
    "roe_pct": "ROE, %",
    "assets_mln": "Активы",          # Chart 1 — структура капитала
    "equity_mln": "Капитал",
}
HIST_YEARS = 8


def _num(v):
    return None if pd.isna(v) else round(float(v), 1)


def history_block(sub: pd.DataFrame) -> dict:
    sub = sub.sort_values("year").tail(HIST_YEARS)
    cols = [c for c in HIST_FIELDS if c in sub.columns]
    # обрезаем хвостовые годы без данных (иначе ось показывает год, где линии нет)
    while len(sub) > 1 and cols and all(pd.isna(sub.iloc[-1][c]) for c in cols):
        sub = sub.iloc[:-1]
    b = {"years": [int(y) for y in sub["year"]], "labels": HIST_FIELDS}
    for c in HIST_FIELDS:
        if c in sub.columns:
            b[c] = [_num(v) for v in sub[c]]
    return b


def sensitivity_block(s) -> dict | None:
    if s is None:
        return None
    return {
        "row_label": s.index.name, "col_label": s.columns.name,
        "rows": [round(float(x), 4) for x in s.index],
        "cols": [round(float(c), 4) for c in s.columns],
        "values": [[None if pd.isna(v) else round(float(v), 0) for v in row] for row in s.values],
    }


def main() -> int:
    art = json.load(open(ARTIFACT, encoding="utf-8"))
    panel = pd.read_csv(PANEL)

    dividends = {r["ticker"]: r.get("dividend_forecast") for r in art["tickers"]
                 if isinstance(r.get("dividend_forecast"), (int, float))}
    # цены из панели (last price_end) — для весов WACC; upside пересчитает build_data к свежей
    prices = {}
    for tk, sub in panel.groupby("ticker"):
        p = sub.sort_values("year")["price_end"].dropna()
        if len(p):
            prices[str(tk)] = float(p.iloc[-1])

    try:
        rf = MoexMarket().risk_free_rate()
    except Exception as e:  # noqa: BLE001
        rf = 0.15
        print(f"[build_valuations] Rf (ОФЗ) недоступна: {e} → 0.15")

    # сектор-медианы мультипликаторов для сравнительного метода (робастно, без аутлаеров)
    sector_mult = {}
    last_rows = panel.sort_values("year").groupby("ticker").tail(1)
    for sec, grp in last_rows.groupby("sector"):
        def med(col, lo, hi):
            s = pd.to_numeric(grp[col], errors="coerce").dropna() if col in grp.columns else pd.Series(dtype=float)
            s = s[(s > lo) & (s < hi)]
            return round(float(s.median()), 2) if len(s) >= 3 else None
        sector_mult[str(sec)] = {"ev_ebitda": med("ev_ebitda", 0, 30), "pe": med("pe", 0, 40),
                                 "ps": med("ps", 0, 15)}

    router = ValuationRouter(panel, dividends=dividends, prices=prices, rf=rf, sector_mult=sector_mult)

    n_val = n_hist = 0
    by_method: dict = {}
    for r in art["tickers"]:
        tk = r["ticker"]
        sub = panel[panel["ticker"] == tk]
        try:
            vr = router.value(tk)
            r["valuation"] = {
                "method": vr.method, "vclass": vr.vclass,
                "fair_price": None if vr.fair_price != vr.fair_price else round(vr.fair_price, 1),
                "note": vr.note, "alert": vr.alert,
                "assumptions": {k: (round(v, 3) if isinstance(v, float) else v)
                                for k, v in vr.assumptions.items()},
                "sensitivity": sensitivity_block(vr.sensitivity),
            }
            n_val += 1
            by_method[vr.vclass] = by_method.get(vr.vclass, 0) + 1
        except Exception as e:  # noqa: BLE001
            r["valuation"] = {"method": "н/д", "vclass": "?", "fair_price": None,
                              "note": f"оценка не построилась: {e}", "alert": "",
                              "assumptions": {}, "sensitivity": None}
        if len(sub):
            r["history"] = history_block(sub)
            n_hist += 1

    # ── cross-sectional Governance / Capital-Allocation флаг ──
    # Δ(DCF,DDM) высок у ВСЕХ сырьевиков при дорогом ключе (системно). Идиосинкразию (запертый
    # кэш СВЕРХ системного эффекта) ловим относительно: робастный z к медиане сектора COMMODITY/MATURE.
    GOV_Z, GOV_FLOOR, GOV_CAP = 1.5, 40, 250   # σ-порог, пол Δ%, потолок (выше — оценка сломана) — тюнится здесь
    pairs = [(r, (r.get("valuation") or {}).get("assumptions", {}).get("delta_pct"))
             for r in art["tickers"] if (r.get("valuation") or {}).get("vclass") in ("COMMODITY", "MATURE")]
    sane = [d for _, d in pairs if isinstance(d, (int, float)) and d <= GOV_CAP]
    n_gov = n_broken = 0
    if sane:
        med = statistics.median(sane)
        mad = statistics.median([abs(x - med) for x in sane]) or 1.0
        for r, d in pairs:
            if not isinstance(d, (int, float)):
                continue
            v = r["valuation"]
            if d > GOV_CAP:                          # сломанная оценка — не governance
                v["alert"] = "Оценка ненадёжна: DCF↔DDM расходятся аномально (вероятна ошибка данных/валюты)."
                n_broken += 1
                continue
            z = (d - med) / (1.4826 * mad)
            v["assumptions"]["gov_z"] = round(z, 1)
            if d >= GOV_FLOOR and z >= GOV_Z:        # аутлаер к сектору → идиосинкразия
                v["alert"] = (f"Governance / Capital Allocation (выше сектора на {z:.1f}σ): "
                              f"Δ(DCF↔DDM) {d:.0f}% при медиане {med:.0f}% — FCF не доходит до "
                              f"акционера сверх системного эффекта ставки (запертый кэш / неэфф. M&A).")
                n_gov += 1
        print(f"  governance-флаг: {n_gov} | оценка-ненадёжна (Δ>{GOV_CAP}%): {n_broken} | медиана Δ={med:.0f}%")

    art["meta"]["valuation_asof"] = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    art["meta"]["rf_ofz"] = round(rf, 4)
    json.dump(art, open(ARTIFACT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[build_valuations] оценка: {n_val} | история: {n_hist} | Rf(ОФЗ)={rf:.1%}")
    print(f"  по классам: {by_method}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
