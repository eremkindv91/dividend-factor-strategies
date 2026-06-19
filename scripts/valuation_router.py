#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Роутер оценки справедливой стоимости (паттерн Strategy): метод выбирается по КЛАССУ
эмитента, а не один на всех (DCF для всех — методологически неверно).

Алгоритм (по спеке аналитика):
  SPECIAL (SNGSP/LSNGP…)        → NAV/Special (флаг, без автонома): ставка на кубышку/устав.
  HOLDING (Конгломерат)         → SOTP (флаг): сумма долей дочек − долг корп-центра.
  REALESTATE (Недвижимость)     → NAV (флаг): оценка портфеля объектов.
  GROWTH (IT)                   → мультипликаторы: тек. EV/EBITDA(fwd) vs 3y-медиана → таргет.
  FINANCIAL (Банки/Финансы)     → DDM, g=4% (FCFF к банку неприменим).
  REGULATED (Энергетика/Телеком)→ DCF + DDM(g=1%, тариф-регулирование), долговой гейт.
  COMMODITY/MATURE (прочее)     → DCF (FCFF) + DDM(g=2–2.5%), долговой гейт.

Долговой гейт: Net Debt/EBITDA > 2.5× → дивиденд «в долг» (риск среза → 100%) → DDM ОТКЛ,
только DCF + алерт. Re = Rf(ОФЗ) + β·ERP; при текущем ключе Re≈22% → DDM-таргет жёсткий
(справедливая дивдоходность ~Re−g), что отражает реальность рынка.

Источник дивиденда D1 — прогноз модели (model_output/forecast_rf.json).
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
from dcf_valuation import DCFValuation, MoexMarket, ERP_RU  # noqa: E402

# ── классы эмитентов по сектору панели ──
FINANCIAL = {"Финансы (Банки)", "Финансы"}
GROWTH = {"IT"}
HOLDING = {"Конгломерат"}
REALESTATE = {"Недвижимость"}
REGULATED = {"Энергетика", "Телекоммуникации"}           # тариф → низкий g
COMMODITY = {"Нефть и газ", "Металлы и добыча", "Химия"}
# прочее (Промышленность, Потребительский, Транспорт, Здравоохранение) → MATURE

SPECIAL_TICKERS = {"SNGSP", "SNGS", "LSNGP"}             # NAV/спец-ситуация (расширяемо)
DDM_G = {"FINANCIAL": 0.04, "REGULATED": 0.01, "COMMODITY": 0.025, "MATURE": 0.02}
ND_EBITDA_MAX = 2.5                                      # выше → дивиденд в долг → DDM off
FROZEN = os.path.join(REPO, "model_output", "forecast_rf.json")


def classify(ticker: str, sector: str) -> str:
    if ticker in SPECIAL_TICKERS:
        return "SPECIAL"
    if sector in HOLDING:
        return "HOLDING"
    if sector in REALESTATE:
        return "REALESTATE"
    if sector in FINANCIAL:
        return "FINANCIAL"
    if sector in GROWTH:
        return "GROWTH"
    if sector in REGULATED:
        return "REGULATED"
    if sector in COMMODITY:
        return "COMMODITY"
    return "MATURE"


def _latest(sub: pd.DataFrame, c: str, default=np.nan) -> float:
    s = sub[c].dropna() if c in sub.columns else pd.Series(dtype=float)
    return float(s.iloc[-1]) if len(s) else default


def _shares(sub: pd.DataFrame, price: Optional[float]) -> float:
    s = _latest(sub, "number_of_shares")
    if s and s > 0:
        return s
    mc = _latest(sub, "market_cap_mln")
    return mc / price if (mc and price and price > 0) else float("nan")


@dataclasses.dataclass
class ValuationResult:
    ticker: str
    sector: str
    vclass: str
    method: str
    fair_price: float
    current_price: Optional[float]
    upside_pct: float
    assumptions: dict
    note: str
    alert: str = ""
    sensitivity: Optional[pd.DataFrame] = None

    def line(self) -> str:
        fair = f"{self.fair_price:,.1f}₽" if self.fair_price == self.fair_price else "—"
        up = f"{self.upside_pct:+.0f}%" if self.upside_pct == self.upside_pct else "—"
        al = f"  ⚠ {self.alert}" if self.alert else ""
        return f"{self.ticker:6s} [{self.vclass:10s}] {self.method:18s} fair={fair:>11s} upside={up:>6s}{al}"


class DDM:
    """Модель Гордона: P0 = D1 / (Re − g)."""

    def __init__(self, d1: float, re: float, g: float):
        self.d1, self.re, self.g = d1, re, g

    def fair(self, re: Optional[float] = None, g: Optional[float] = None) -> float:
        re = self.re if re is None else re
        g = self.g if g is None else g
        return self.d1 / (re - g) if re > g else float("nan")

    def sensitivity(self, re_range, g_range) -> pd.DataFrame:
        m = pd.DataFrame({round(g, 4): [self.fair(re=r, g=g) for r in re_range] for g in g_range},
                         index=[round(r, 4) for r in re_range])
        m.index.name, m.columns.name = "Re", "g"
        return m


class ValuationRouter:
    def __init__(self, panel: pd.DataFrame, dividends: Optional[Dict[str, float]] = None,
                 market: Optional[MoexMarket] = None, rf: Optional[float] = None,
                 prices: Optional[Dict[str, float]] = None):
        self.panel = panel
        self.dividends = dividends or {}
        self.prices = prices or {}            # batch: цены словарём (без per-ticker API)
        self.market = market or MoexMarket()
        self.rf = rf if rf is not None else self._safe_rf()

    def _safe_rf(self) -> float:
        try:
            return self.market.risk_free_rate()
        except Exception as e:  # noqa: BLE001
            print(f"[warn] Rf (ОФЗ) недоступна: {e} → 0.15")
            return 0.15

    def _price(self, ticker: str, sub: pd.DataFrame) -> Optional[float]:
        if self.prices:                                 # batch-режим: словарь/панель, без API
            return self.prices.get(ticker) or _latest(sub, "price_end")
        try:
            return self.market.last_price(ticker)       # standalone: live
        except Exception:  # noqa: BLE001
            return _latest(sub, "price_end")

    def value(self, ticker: str) -> ValuationResult:
        sub = self.panel[self.panel["ticker"] == ticker].sort_values("year")
        if sub.empty:
            raise ValueError(f"{ticker} нет в панели")
        sector = str(sub["sector"].dropna().iloc[-1]) if sub["sector"].notna().any() else "?"
        vclass = classify(ticker, sector)
        price = self._price(ticker, sub)
        beta = _latest(sub, "beta_imoex", 1.0) or 1.0
        re = self.rf + beta * ERP_RU
        nd_eb = _latest(sub, "net_debt_to_ebitda")
        div = self.dividends.get(ticker)

        def res(method, fair, assum, note, alert="", sens=None):
            up = (fair / price - 1) * 100 if (price and fair == fair) else float("nan")
            return ValuationResult(ticker, sector, vclass, method, fair, price, up, assum, note, alert, sens)

        # ── флаговые классы (без автонома) ──
        if vclass == "SPECIAL":
            return res("NAV / Special", float("nan"), {},
                       "Стоимость — ставка на валютную кубышку / спец-актив; механическая оценка неинформативна (NAV вручную).")
        if vclass == "HOLDING":
            return res("SOTP", float("nan"), {},
                       "Справедливая стоимость = рыночная оценка публичных и непубличных дочек − долг корп-центра. Механически не считаем.")
        if vclass == "REALESTATE":
            return res("NAV", float("nan"), {},
                       "Оценка по NAV портфеля объектов недвижимости. Механически не считаем.")

        # ── GROWTH → мультипликаторы ──
        if vclass == "GROWTH":
            return self._multiples(sub, price, res)

        # ── FINANCIAL → DDM(g=4%) ──
        if vclass == "FINANCIAL":
            g = DDM_G["FINANCIAL"]
            if not (div and div > 0):
                return res("DDM", float("nan"), {"Re": round(re, 3), "g": g},
                           "Банк без прогноза дивиденда → DDM неинформативен (смотри justified P/B = (ROE−g)/(Re−g)).",
                           alert="нет дивиденда")
            ddm = DDM(div, re, g)
            sens = ddm.sensitivity([re - 0.04, re - 0.02, re, re + 0.02, re + 0.04], [0.03, 0.04, 0.05])
            return res("DDM", ddm.fair(), {"D1": div, "Re": round(re, 3), "g": g},
                       f"Банк: DDM, g={g:.0%} (органический рост капитала ~инфляция/ВВП). FCFF к банку неприменим.",
                       sens=sens)

        # ── REGULATED / COMMODITY / MATURE ──
        # DCF (с нормализацией цикликов) → если базовый FCFF<0 → блок; дивфишка → DDM-якорь.
        cyc = (vclass == "COMMODITY")
        dcf = None
        base_fcff = dcf_fair = wacc = float("nan")
        try:
            dcf = DCFValuation.from_panel(ticker, self.panel, price=price, rf=self.rf,
                                          normalize_cyclical=cyc)
            dff, s = dcf.valuate()
            base_fcff = float(dff["FCFF"].iloc[0])
            dcf_fair, wacc = s["Fair_Price"], s["WACC"]
        except Exception:  # noqa: BLE001
            pass
        dcf_ok = (base_fcff == base_fcff and base_fcff > 0 and dcf_fair == dcf_fair and dcf_fair > 0)
        g_ddm = DDM_G.get(vclass, DDM_G["MATURE"])

        note = ""
        if vclass == "REGULATED":
            note += "Тариф-регулирование → низкий g. "
        if cyc:
            note += "Циклик: входы нормализованы по медиане (мид-цикл). "

        def dcf_sens():
            return dcf.sensitivity_matrix([wacc - 0.04, wacc - 0.02, wacc, wacc + 0.02, wacc + 0.04],
                                          [0.01, 0.02, 0.03]) if dcf else None

        # долговой гейт: дивиденд в долг → DDM отключён
        if div and div > 0 and nd_eb == nd_eb and nd_eb > ND_EBITDA_MAX:
            alert = f"Net Debt/EBITDA {nd_eb:.1f}× (>{ND_EBITDA_MAX}) — дивиденд в долг, риск среза; DDM отключён."
            if dcf_ok:
                return res("DCF", dcf_fair, {"WACC": round(wacc, 3), "g": dcf.g}, note, alert, dcf_sens())
            return res("DCF — нерепрезентативен", float("nan"), {},
                       note + "DCF нерепрезентативен: отрицательный FCF (пик инвестцикла).", alert)

        # дивидендная фишка → ЯКОРЬ DDM (механический DCF на высокой ставке завышает)
        if div and div > 0:
            ddm = DDM(div, re, g_ddm)
            sens = ddm.sensitivity([re - 0.04, re - 0.02, re, re + 0.02, re + 0.04],
                                   [max(0.0, g_ddm - 0.01), g_ddm, g_ddm + 0.01])
            assum = {"D1": div, "Re": round(re, 3), "g": g_ddm}
            n = note + f"Дивидендная фишка → якорь DDM (g={g_ddm:.0%}). "
            if dcf_ok:
                assum["DCF_fair"] = round(dcf_fair, 1)
                n += f"DCF-кросс-чек: {dcf_fair:,.0f}₽."
            else:
                n += "DCF скрыт: отрицательный FCF (пик инвестцикла)."
            return res("DDM", ddm.fair(), assum, n, sens=sens)

        # не дивидендная: DCF если репрезентативен, иначе блок
        if dcf_ok:
            return res("DCF", dcf_fair, {"WACC": round(wacc, 3), "g": dcf.g}, note, sens=dcf_sens())
        return res("DCF — нерепрезентативен", float("nan"), {},
                   note + "DCF нерепрезентативен: отрицательный свободный денежный поток (пик инвестцикла).")

    def _multiples(self, sub, price, res):
        shares = _shares(sub, price)
        nd = _latest(sub, "net_debt_mln", 0.0)
        eb = sub["ebitda_mln"].dropna()
        evm = sub["ev_ebitda"].dropna()
        if len(eb) and eb.iloc[-1] > 0 and len(evm):
            cur, med, ebitda = evm.iloc[-1], float(evm.tail(3).median()), eb.iloc[-1]
            target_ev, metric = med * ebitda, "EV/EBITDA"
        else:                                            # ранний рост без EBITDA → EV/Sales
            rev = sub["revenue_mln"].dropna()
            mcap = (price or 0) * shares
            cur = (mcap + nd) / rev.iloc[-1] if len(rev) and rev.iloc[-1] else float("nan")
            med, target_ev, metric = 1.5, 1.5 * (rev.iloc[-1] if len(rev) else 0), "EV/Sales"
        fair = (target_ev - nd) / shares if (shares == shares and shares > 0) else float("nan")
        note = (f"Оценка по историческим форвардным мультипликаторам ({metric}): тек.={cur:.1f}× vs "
                f"3y-медиана={med:.1f}×. Свободный денежный поток нерепрезентативен (стадия активного роста).")
        return res(f"Multiples ({metric})", fair,
                   {"current_mult": round(cur, 1), "target_mult": round(med, 1)}, note)


def load_dividends(path: str = FROZEN) -> Dict[str, float]:
    """D1 = прогнозный дивиденд из forecast_rf.json."""
    try:
        d = json.load(open(path, encoding="utf-8"))
        return {r["ticker"]: r.get("dividend_forecast") for r in d["tickers"]
                if isinstance(r.get("dividend_forecast"), (int, float))}
    except Exception as e:  # noqa: BLE001
        print(f"[warn] нет forecast_rf.json ({e}) — DDM без дивидендов")
        return {}


if __name__ == "__main__":
    panel = pd.read_csv(os.path.join(REPO, "data", "panels_final", "panel_russia_final.csv"))
    router = ValuationRouter(panel, dividends=load_dividends(), rf=0.155)   # rf фикс для примера
    print(f"Rf={router.rf:.1%}  ERP={ERP_RU:.0%}\n")
    for tk in ["SBER", "YNDX", "OZON", "AFKS", "SNGSP", "PIKK", "LKOH",
               "MTSS", "SGZH", "GMKN", "TATN", "MGNT"]:
        try:
            print(router.value(tk).line())
        except Exception as e:  # noqa: BLE001
            print(f"{tk:6s} ОШИБКА: {e}")
