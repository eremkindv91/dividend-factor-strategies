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
import statistics
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
                 prices: Optional[Dict[str, float]] = None,
                 sector_mult: Optional[Dict[str, dict]] = None):
        self.panel = panel
        self.dividends = dividends or {}
        self.prices = prices or {}            # batch: цены словарём (без per-ticker API)
        self.sector_mult = sector_mult or {}  # {сектор: {ev_ebitda, pe}} для сравнительного метода
        self._tickers = set(panel["ticker"].astype(str))
        self.market = market or MoexMarket()
        self.rf = rf if rf is not None else self._safe_rf()

    def _comparative(self, sub: pd.DataFrame, sector: str, price) -> Optional[float]:
        """Сравнительная оценка: медиана EV/EBITDA и P/E сектора → implied справедливая цена."""
        m = self.sector_mult.get(sector, {})
        shares = _shares(sub, price)
        if not (shares and shares > 0):
            return None
        nd = _latest(sub, "net_debt_mln", 0.0)
        fairs = []
        ebitda = _latest(sub, "ebitda_mln")
        if m.get("ev_ebitda") and ebitda and ebitda > 0:
            fairs.append((m["ev_ebitda"] * ebitda - nd) / shares)
        ni = _latest(sub, "net_profit_mln")
        if m.get("pe") and ni and ni > 0:
            fairs.append(m["pe"] * ni / shares)
        return statistics.median(fairs) if fairs else None

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
        is_pref = ticker.endswith("P") and ticker[:-1] in self._tickers

        def res(method, fair, assum, note, alert="", sens=None):
            up = (fair / price - 1) * 100 if (price and fair == fair) else float("nan")
            if fair == fair and up == up and abs(up) > 300:    # абсурд → не показываем как опорный
                return ValuationResult(ticker, sector, vclass, "Оценка ненадёжна", float("nan"), price,
                                       float("nan"), {},
                                       "Оценка ненадёжна: |upside|>300% (данные / разводнение / преф).", alert, None)
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

        # ── GROWTH → сектор-мультипликаторы ──
        if vclass == "GROWTH":
            return self._multiples(sub, sector, price, res)

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

        # ── ПРЕФЫ (дивидендный инструмент): DCF делит equity на share-base префа → ломается → DDM ──
        if is_pref and div and div > 0:
            g = DDM_G.get(vclass, DDM_G["MATURE"])
            ddm = DDM(div, re, g)
            sens = ddm.sensitivity([re - 0.04, re - 0.02, re, re + 0.02, re + 0.04],
                                   [max(0.0, g - 0.01), g, g + 0.01])
            return res("DDM (преф)", ddm.fair(), {"D1": div, "Re": round(re, 3), "g": g},
                       f"Привилегированная (дивидендный инструмент) → DDM, g={g:.0%}.", sens=sens)

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

        def ddm_result(extra_note):
            ddm = DDM(div, re, g_ddm)
            sens = ddm.sensitivity([re - 0.04, re - 0.02, re, re + 0.02, re + 0.04],
                                   [max(0.0, g_ddm - 0.01), g_ddm, g_ddm + 0.01])
            assum = {"D1": div, "Re": round(re, 3), "g": g_ddm}
            n = note + extra_note
            if dcf_ok:
                assum["DCF_fair"] = round(dcf_fair, 1)
                n += f" DCF-кросс-чек: {dcf_fair:,.0f}₽."
            return res("DDM", ddm.fair(), assum, n, sens=sens)

        # РЕГУЛИРУЕМЫЕ (тариф → механический DCF завышает) + дивиденд → ЯКОРЬ DDM
        if vclass == "REGULATED" and div and div > 0:
            return ddm_result(f"Регулируемый дивиденд → якорь DDM (g={g_ddm:.0%}).")

        # COMMODITY / MATURE (зрелый ген-кэша) → ЯКОРЬ DCF (если FCFF>0), DDM — кросс-чек
        if dcf_ok:
            comp = self._comparative(sub, sector, price)
            assum = {"WACC": round(wacc, 3), "g": dcf.g, "DCF_fair": round(dcf_fair, 1)}
            if div and div > 0:
                ddm_fair = DDM(div, re, g_ddm).fair()
                assum["DDM_fair"] = round(ddm_fair, 1)
                # Δ = |P_DCF − P_DDM| / P_DCF — governance-флаг ставится КРОСС-СЕКЦИОННО (build_valuations)
                if ddm_fair == ddm_fair and dcf_fair > 0:
                    assum["delta_pct"] = round(abs(dcf_fair - ddm_fair) / dcf_fair * 100)
            if comp and comp > 0:                              # справедливая = среднее DCF↔сравнит.
                blend = (dcf_fair + comp) / 2
                assum["Comp_fair"] = round(comp, 1)
                n = note + (f"Справедливая = среднее DCF↔сравнит. (сектор-мультипл.): "
                            f"DCF {dcf_fair:,.0f} / Comp {comp:,.0f} → {blend:,.0f}₽.")
                if div and div > 0:
                    n += f" DDM-кросс-чек: {assum.get('DDM_fair', 0):,.0f}₽."
                return res("DCF + Сравнит. (среднее)", blend, assum, n, sens=dcf_sens())
            n = note + "Зрелый генератор кэша → якорь DCF (FCFF)."
            if div and div > 0:
                n += f" DDM-кросс-чек (g={g_ddm:.0%}): {assum.get('DDM_fair', 0):,.0f}₽."
            return res("DCF", dcf_fair, assum, n, sens=dcf_sens())

        # DCF блокирован (FCFF<0): дивиденд → DDM-фолбэк, иначе «нерепрезентативен»
        if div and div > 0:
            return ddm_result("DCF скрыт (отрицательный FCF, пик инвестцикла) → оценка по DDM.")
        return res("DCF — нерепрезентативен", float("nan"), {},
                   note + "DCF нерепрезентативен: отрицательный свободный денежный поток (пик инвестцикла).")

    def _multiples(self, sub, sector, price, res):
        """GROWTH/IT — оценка по СЕКТОР-мультипликаторам (медиана EV/EBITDA + P/E), не по своей
        волатильной истории; P/S-фолбэк для ранних без EBITDA. Сан-клэмп ловит хвосты."""
        m = self.sector_mult.get(sector, {})
        shares = _shares(sub, price)
        comp = self._comparative(sub, sector, price)         # сектор-медиана EV/EBITDA + P/E
        if comp and comp > 0:
            return res("Сравнит. (сектор)", comp,
                       {"ev_ebitda_sec": m.get("ev_ebitda"), "pe_sec": m.get("pe")},
                       f"GROWTH/IT → сектор-мультипликаторы (EV/EBITDA {m.get('ev_ebitda')}× / "
                       f"P/E {m.get('pe')}×). FCFF нерепрезентативен (стадия роста).")
        rev = _latest(sub, "revenue_mln")                    # ранний рост без EBITDA/прибыли → P/S сектора
        ps = m.get("ps")
        if ps and rev and rev > 0 and shares and shares > 0:
            return res("Сравнит. (P/S сектора)", ps * rev / shares, {"ps_sec": ps},
                       f"GROWTH без EBITDA → P/S сектора {ps}×. Прибыль/FCFF нерепрезентативны (ранний рост).")
        return res("Сравнит. — н/д", float("nan"), {}, "GROWTH: нет сектор-мультипликаторов для оценки.")


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
