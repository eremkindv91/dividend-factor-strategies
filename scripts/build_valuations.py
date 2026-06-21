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

# ── сектор-перцентили: коэффициенты деривируем из СЫРЫХ полей (ratio-колонки разрежены) ──
PCTL_YEAR_MIN = 2023          # срез кросс-секции: не сравнивать ROE-2017 с ROE-2025
PCTL_MIN_PEERS = 5            # < сопоставимых в секторе → метрику не показываем (шум)
PCTL_META = {                 # key → (подпись, единица, polarity 'up'|'down', знаков после запятой)
    "roe":           ("ROE", "%", "up", 1),
    "ebitda_margin": ("EBITDA-маржа", "%", "up", 1),
    "debt_ebitda":   ("Долг / EBITDA", "×", "down", 2),
    "div_yield":     ("Дивдоходность", "%", "up", 1),
    "stability":     ("Устойчивость дивиденда", "", "up", 2),
}
PCTL_ORDER = ["roe", "ebitda_margin", "debt_ebitda", "div_yield", "stability"]


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


def _pct_rank(vals: list, x: float) -> float:
    """Перцентиль x в vals (kind='mean'), 0..100. Без scipy — не тащим зависимость в CI."""
    n = len(vals)
    below = sum(1 for v in vals if v < x)
    equal = sum(1 for v in vals if v == x)
    return 100.0 * (below + 0.5 * equal) / n


def _fundamentals(sub: pd.DataFrame) -> dict:
    """5 фундаментальных коэффициентов из СЫРЫХ полей (надёжнее разреженных ratio-колонок).
    Потоковые (ROE/маржа/долг) берём из последнего ПОЛНОГО года = последней строки с непустой
    выручкой: частичный текущий год выручки часто не имеет, иначе ROE искажался бы (прибыль за
    квартал / полный капитал). Доходность — из самой свежей строки (рыночная метрика)."""
    sub = sub.sort_values("year")
    sub = sub[sub["year"] >= PCTL_YEAR_MIN]
    if sub.empty:
        return {}
    last = sub.iloc[-1]
    rev = pd.to_numeric(sub["revenue_mln"], errors="coerce") if "revenue_mln" in sub else pd.Series(dtype=float)
    rev_rows = sub[rev.notna().values] if len(rev) else sub.iloc[0:0]
    frow = rev_rows.iloc[-1] if len(rev_rows) else last      # строка «полного» года
    def g(row, c):
        return float(row[c]) if (c in row and pd.notna(row[c])) else None
    np_, eq, rv, eb = g(frow, "net_profit_mln"), g(frow, "equity_mln"), g(frow, "revenue_mln"), g(frow, "ebitda_mln")
    nd = g(frow, "net_debt_mln")
    if nd is None:
        nd = g(frow, "total_debt_mln")       # net_debt разрежён → gross-leverage как фолбэк
    return {
        "roe":           (np_ / eq * 100) if (np_ is not None and eq and eq > 0) else None,
        "ebitda_margin": (eb / rv * 100) if (eb is not None and rv and rv > 0) else None,
        "debt_ebitda":   (nd / eb) if (nd is not None and eb and eb > 0) else None,
        "div_yield":     g(last, "div_yield_pct"),
    }


def attach_sector_percentiles(art_tickers: list, panel: pd.DataFrame) -> int:
    """Перцентиль каждой метрики ВНУТРИ сектора, ориентированный «к лучшему» (good_pct: 100=лучший).
    Гейт N≥PCTL_MIN_PEERS заодно гасит неприменимые метрики (у банков EBITDA-маржа/Долг почти пусты)."""
    fund = {str(tk): _fundamentals(sub) for tk, sub in panel.groupby("ticker")}
    pools: dict = {}                          # сектор → метрика → {тикер: значение}
    for r in art_tickers:
        tk, sec = r["ticker"], r.get("sector")
        vals = dict(fund.get(tk, {}))
        vals["stability"] = r.get("stability_score")   # из артефакта (полное покрытие)
        for m, v in vals.items():
            if isinstance(v, (int, float)) and v == v:  # не None/NaN
                pools.setdefault(sec, {}).setdefault(m, {})[tk] = float(v)
    n_blocks = 0
    for r in art_tickers:
        tk, sec = r["ticker"], r.get("sector")
        out = []
        for m in PCTL_ORDER:
            pool = pools.get(sec, {}).get(m, {})
            if tk not in pool or len(pool) < PCTL_MIN_PEERS:
                continue
            label, unit, pol, ndig = PCTL_META[m]
            raw_pct = _pct_rank(list(pool.values()), pool[tk])
            good = raw_pct if pol == "up" else (100.0 - raw_pct)
            out.append({"key": m, "label": label, "unit": unit, "polarity": pol,
                        "raw": round(pool[tk], ndig), "good_pct": int(round(good)), "n": len(pool)})
        if out:
            r["sector_percentiles"] = {"sector": sec, "metrics": out}
            n_blocks += 1
    return n_blocks


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
            sv = sub.sort_values("year")
            ndv = (pd.to_numeric(sv["net_debt_to_ebitda"], errors="coerce").dropna()
                   if "net_debt_to_ebitda" in sub.columns else pd.Series(dtype=float))
            r["nd_ebitda"] = round(float(ndv.iloc[-1]), 2) if len(ndv) else None   # для долгового штрафа вердикта
            # implied shares (mcap/price посл. года) → build_data считает живой mcap для value-weight/пенни-фильтра
            mc = pd.to_numeric(sv["market_cap_mln"], errors="coerce") if "market_cap_mln" in sv.columns else pd.Series(dtype=float)
            pe = pd.to_numeric(sv["price_end"], errors="coerce") if "price_end" in sv.columns else pd.Series(dtype=float)
            both = mc.notna() & pe.notna() & (pe > 0)
            r["shares"] = round(float(mc[both].iloc[-1]) * 1e6 / float(pe[both].iloc[-1])) if both.any() else None
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

    n_pct = attach_sector_percentiles(art["tickers"], panel)
    print(f"  сектор-перцентили: блоков {n_pct}")

    art["meta"]["valuation_asof"] = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    art["meta"]["rf_ofz"] = round(rf, 4)
    json.dump(art, open(ARTIFACT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[build_valuations] оценка: {n_val} | история: {n_hist} | Rf(ОФЗ)={rf:.1%}")
    print(f"  по классам: {by_method}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
