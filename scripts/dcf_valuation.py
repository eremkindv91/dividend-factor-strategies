#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DCF (Discounted Cash Flow) — справедливая стоимость НЕфинансовой компании MOEX.

FCFF-модель: 5-летний явный период + терминал по Гордону.
  • Динамика (цена TQBR, безрисковая ставка Rf = YTM длинной ОФЗ) — c MOEX ISS «в моменте»
    (ключ скачет → Rf хардкодить нельзя).
  • Фундаментал (МСФО) — вручную словарём (Fundamentals) ИЛИ из панели (.from_panel) для
    квартального автонома. Ручной путь точнее (РФ-компании публикуют урезанную отчётность).

НЕ для банков/страховых (FCFF/EBIT не применимы к финсектору).

Два режима:
  manual:  DCFValuation("LKOH", Fundamentals(...))
  auto:    DCFValuation.from_panel("LKOH", panel_df)        # для refresh.yml (квартально)

Запуск примера:  python scripts/dcf_valuation.py
"""
from __future__ import annotations

import dataclasses
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

ISS = "https://iss.moex.com/iss"
DEFAULT_OFZ = "SU26238RMFS4"   # длинная ОФЗ (~2041) — прокси 10y+ безриска РФ
ERP_RU = 0.10                  # премия за риск рынка РФ (default 9–10%)


def _rows(block: dict) -> List[dict]:
    return [dict(zip(block["columns"], r)) for r in block["data"]]


# ════════════════════════════════════════════════════════════════════════
#  Рыночные данные MOEX ISS (цена + безрисковая ставка)
# ════════════════════════════════════════════════════════════════════════
class MoexMarket:
    """Текущая цена (борд TQBR) и Rf = YTM длинной ОФЗ (борд TQOB). Только чтение."""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self._s = requests.Session() if requests else None

    def _get(self, url: str, only: str, cols: Dict[str, str]) -> dict:
        if self._s is None:
            raise RuntimeError("requests не установлен")
        params = {"iss.meta": "off", "iss.only": only}
        params.update(cols)
        r = self._s.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def last_price(self, ticker: str) -> float:
        url = f"{ISS}/engines/stock/markets/shares/boards/TQBR/securities/{ticker}.json"
        j = self._get(url, "marketdata,securities",
                      {"marketdata.columns": "SECID,LAST,LCLOSEPRICE",
                       "securities.columns": "SECID,PREVPRICE"})
        for r in _rows(j["marketdata"]):
            for f in ("LAST", "LCLOSEPRICE"):
                if r.get(f):
                    return float(r[f])
        for r in _rows(j["securities"]):
            if r.get("PREVPRICE"):
                return float(r["PREVPRICE"])
        raise RuntimeError(f"нет цены TQBR для {ticker}")

    def risk_free_rate(self, ofz: str = DEFAULT_OFZ) -> float:
        """YTM длинной ОФЗ в долях (напр. 0.155). Прокси Rf."""
        url = f"{ISS}/engines/stock/markets/bonds/boards/TQOB/securities/{ofz}.json"
        j = self._get(url, "marketdata", {"marketdata.columns": "SECID,YIELD,YIELDATWAP"})
        for r in _rows(j["marketdata"]):
            for f in ("YIELD", "YIELDATWAP"):
                if r.get(f):
                    return float(r[f]) / 100.0
        raise RuntimeError(f"нет YTM по {ofz}")


# ════════════════════════════════════════════════════════════════════════
#  Фундаментал (МСФО) — единицы самосогласованы (всё в млрд ИЛИ всё в млн)
# ════════════════════════════════════════════════════════════════════════
@dataclasses.dataclass
class Fundamentals:
    revenue_ltm: float        # выручка LTM
    ebit_margin: float        # EBIT-маржа (доля)
    da_pct: float             # D&A в % от выручки (доля)
    capex_pct: float          # CAPEX в % от выручки (доля)
    nwc_pct: float            # оборотный капитал в % от выручки (доля)
    tax_rate: float           # эффективная ставка налога (доля)
    net_debt: float           # чистый долг (для bridge EV→Equity)
    shares: float             # кол-во акций (в той же базе млрд/млн, что и деньги)
    total_debt: Optional[float] = None   # gross-долг для весов WACC (если None → max(net_debt,0))


# ════════════════════════════════════════════════════════════════════════
#  DCF-модель
# ════════════════════════════════════════════════════════════════════════
class DCFValuation:
    YEARS = 5

    def __init__(self, ticker: str, fundamentals: Fundamentals, *,
                 beta: float = 1.0, erp: float = ERP_RU, cost_of_debt: Optional[float] = None,
                 g: float = 0.02, rf: Optional[float] = None, price: Optional[float] = None,
                 market: Optional[MoexMarket] = None):
        self.ticker = ticker.upper()
        self.f = fundamentals
        self.beta, self.erp, self.g = beta, erp, g
        self._cod = cost_of_debt
        self.market = market or MoexMarket()
        # дефолтные допущения (переопределяются set_growth)
        self.rev_cagr: List[float] = [0.05] * self.YEARS
        self.margin_path: List[float] = [fundamentals.ebit_margin] * self.YEARS
        # рыночные данные (с фолбэком при недоступности API)
        self.rf = rf if rf is not None else self._safe_rf()
        self.price = price if price is not None else self._safe_price()

    # ── рыночные данные с обработкой ошибок ──
    def _safe_price(self) -> Optional[float]:
        try:
            return self.market.last_price(self.ticker)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] цена {self.ticker} с MOEX недоступна: {e} → задайте price=...")
            return None

    def _safe_rf(self) -> float:
        try:
            return self.market.risk_free_rate()
        except Exception as e:  # noqa: BLE001
            print(f"[warn] Rf (ОФЗ) с MOEX недоступна: {e} → дефолт 15%")
            return 0.15

    # ── допущения ──
    def set_growth(self, revenue_cagr: Sequence[float],
                   ebit_margin: Optional[Sequence[float]] = None) -> "DCFValuation":
        """Темпы роста выручки по годам (5) и опц. путь EBIT-маржи по годам (5)."""
        if len(revenue_cagr) != self.YEARS:
            raise ValueError(f"revenue_cagr: нужно {self.YEARS} значений")
        self.rev_cagr = list(revenue_cagr)
        if ebit_margin is not None:
            if len(ebit_margin) != self.YEARS:
                raise ValueError(f"ebit_margin: нужно {self.YEARS} значений")
            self.margin_path = list(ebit_margin)
        return self

    # ── ставки ──
    @property
    def cost_of_equity(self) -> float:        # CAPM
        return self.rf + self.beta * self.erp

    @property
    def cost_of_debt(self) -> float:
        return self._cod if self._cod is not None else self.rf + 0.02   # Rd = Rf + спред

    def wacc(self) -> float:
        f = self.f
        E = (self.price or 0.0) * f.shares                       # рыночная капитализация
        D = f.total_debt if f.total_debt is not None else max(f.net_debt, 0.0)
        V = E + D
        if V <= 0:
            return self.cost_of_equity
        we, wd = E / V, D / V
        return we * self.cost_of_equity + wd * self.cost_of_debt * (1 - f.tax_rate)

    # ── прогноз FCFF ──
    def project(self) -> pd.DataFrame:
        f = self.f
        rows, rev_prev, nwc_prev = [], f.revenue_ltm, f.nwc_pct * f.revenue_ltm
        for y in range(1, self.YEARS + 1):
            g, m = self.rev_cagr[y - 1], self.margin_path[y - 1]
            rev = rev_prev * (1 + g)
            ebit = rev * m
            nopat = ebit * (1 - f.tax_rate)
            da, capex, nwc = f.da_pct * rev, f.capex_pct * rev, f.nwc_pct * rev
            d_nwc = nwc - nwc_prev
            fcff = nopat + da - capex - d_nwc                    # FCFF = EBIT(1-t)+D&A-CAPEX-ΔNWC
            rows.append({"Год": y, "Выручка": rev, "Рост": g, "EBIT_маржа": m, "EBIT": ebit,
                         "NOPAT": nopat, "D&A": da, "CAPEX": capex, "ΔNWC": d_nwc, "FCFF": fcff})
            rev_prev, nwc_prev = rev, nwc
        return pd.DataFrame(rows)

    # ── оценка ──
    def _equity_per_share(self, wacc: float, g: float, df: pd.DataFrame) -> dict:
        dfac = [1.0 / (1 + wacc) ** y for y in range(1, self.YEARS + 1)]
        pv_fcff = float(np.dot(df["FCFF"].values, dfac))
        if wacc <= g:                                            # модель Гордона невалидна
            return {"valid": False, "fair": float("nan"), "ev": float("nan"),
                    "tv": float("nan"), "pv_tv": float("nan"), "pv_fcff": pv_fcff}
        tv = df["FCFF"].iloc[-1] * (1 + g) / (wacc - g)
        pv_tv = tv * dfac[-1]
        ev = pv_fcff + pv_tv
        equity = ev - self.f.net_debt
        return {"valid": True, "fair": equity / self.f.shares, "ev": ev, "tv": tv,
                "pv_tv": pv_tv, "pv_fcff": pv_fcff, "equity": equity}

    def valuate(self):
        """Возвращает (DataFrame потоков, dict сводки)."""
        wacc = self.wacc()
        df = self.project()
        dfac = [1.0 / (1 + wacc) ** y for y in df["Год"]]
        df["DF"] = dfac
        df["PV_FCFF"] = df["FCFF"] * df["DF"]
        v = self._equity_per_share(wacc, self.g, df)
        upside = (v["fair"] / self.price - 1) * 100 if (self.price and v["valid"]) else float("nan")
        summary = {
            "Rf": self.rf, "Re_CAPM": self.cost_of_equity, "Rd": self.cost_of_debt,
            "beta": self.beta, "ERP": self.erp, "WACC": wacc, "g": self.g,
            "PV(FCFF)": v["pv_fcff"], "TV": v["tv"], "PV(TV)": v["pv_tv"],
            "EV": v["ev"], "Net_Debt": self.f.net_debt, "Equity": v.get("equity", float("nan")),
            "Fair_Price": v["fair"], "Current_Price": self.price, "Upside_%": upside,
        }
        return df, summary

    def fair_price(self, wacc: Optional[float] = None, g: Optional[float] = None) -> float:
        wacc = self.wacc() if wacc is None else wacc
        g = self.g if g is None else g
        return self._equity_per_share(wacc, g, self.project())["fair"]

    def sensitivity_matrix(self, wacc_range: Sequence[float], g_range: Sequence[float]) -> pd.DataFrame:
        """Матрица справедливой цены: индекс = WACC (Y), столбцы = g (X)."""
        data = {round(g, 4): [self.fair_price(wacc=w, g=g) for w in wacc_range] for g in g_range}
        m = pd.DataFrame(data, index=[round(w, 4) for w in wacc_range])
        m.index.name, m.columns.name = "WACC", "g"
        return m

    def report(self) -> pd.DataFrame:
        df, s = self.valuate()
        print(f"\n═══ DCF: {self.ticker} ═══")
        disp = df.copy()
        disp["Рост"] = (disp["Рост"] * 100).round(1).astype(str) + "%"
        disp["EBIT_маржа"] = (disp["EBIT_маржа"] * 100).round(1).astype(str) + "%"
        disp["DF"] = disp["DF"].round(3)
        for c in ("Выручка", "EBIT", "NOPAT", "D&A", "CAPEX", "ΔNWC", "FCFF", "PV_FCFF"):
            disp[c] = disp[c].round(0)
        print(disp.to_string(index=False))
        print(f"\n  Rf={s['Rf']:.1%}  Re(CAPM)={s['Re_CAPM']:.1%}  Rd={s['Rd']:.1%}  "
              f"β={s['beta']}  ERP={s['ERP']:.0%}")
        print(f"  WACC={s['WACC']:.2%}  g={s['g']:.1%}")
        print(f"  EV={s['EV']:,.0f}  − ЧистДолг={s['Net_Debt']:,.0f}  → Equity={s['Equity']:,.0f}")
        cur = f"{s['Current_Price']:,.1f} ₽" if s["Current_Price"] else "н/д"
        fair = f"{s['Fair_Price']:,.1f} ₽" if s["Fair_Price"] == s["Fair_Price"] else "н/д (WACC≤g)"
        up = f"{s['Upside_%']:+.1f}%" if s["Upside_%"] == s["Upside_%"] else "н/д"
        print(f"  Текущая={cur}  |  Справедливая={fair}  |  Upside={up}")
        return df

    # ── автодеривация из панели (для квартального автонома) ──
    @classmethod
    def from_panel(cls, ticker: str, panel: pd.DataFrame, *, year: Optional[int] = None,
                   beta: Optional[float] = None, g: float = 0.02,
                   cost_of_debt: Optional[float] = None, normalize_cyclical: bool = False,
                   **kw) -> "DCFValuation":
        """
        Собрать Fundamentals из строки панели (best-effort, мех. скрин). Деньги в млн ₽.
        Forward-допущения по умолчанию: рост = историч. 3y CAGR выручки (клип 0..15%),
        маржа = последняя EBIT-маржа. β — из panel.beta_imoex, если не задана.
        """
        sub = panel[panel["ticker"] == ticker].sort_values("year")
        if sub.empty:
            raise ValueError(f"{ticker} нет в панели")
        pool = sub if year is None else sub[sub["year"] <= year]

        def latest(c, default=np.nan):   # последнее доступное значение поля (панель залита неравномерно)
            s = pool[c].dropna() if c in pool.columns else pd.Series(dtype=float)
            return float(s.iloc[-1]) if len(s) else default

        rev = latest("revenue_mln")
        if not rev or rev <= 0:
            raise ValueError(f"{ticker}: нет выручки в панели")
        ebitda_last = latest("ebitda_mln", np.nan)
        capex_last = abs(latest("CAPEX_", latest("capex_mln", 0.0)))
        ebit = latest("operating_income_mln", ebitda_last)   # EBIT~оп.приб.; иначе EBITDA-прокси
        ebit_margin = (ebit / rev) if (ebit == ebit and ebit) else 0.15
        capex_pct = (capex_last / rev) if capex_last else 0.06

        if normalize_cyclical:        # циклик: пик инвестцикла искажает последнюю точку → медиана
            def _med_ratio(col):
                if col not in pool.columns:
                    return None
                s2 = pool.dropna(subset=["revenue_mln", col])
                s2 = s2[s2["revenue_mln"] > 0]
                if s2.empty:
                    return None
                return float((s2[col].abs() / s2["revenue_mln"]).tail(7).median())
            # CAPEX > EBITDA (аномалия большой стройки) → историч. медиана CAPEX/Выручка
            if capex_last and ebitda_last == ebitda_last and ebitda_last > 0 and capex_last > ebitda_last:
                cm = _med_ratio("CAPEX_") or _med_ratio("capex_mln")
                if cm:
                    capex_pct = cm
            mm = _med_ratio("operating_income_mln") or _med_ratio("ebitda_mln")  # маржа: медиана (мид-цикл)
            if mm:
                ebit_margin = mm

        shares = latest("number_of_shares", np.nan)
        if not (shares and shares > 0):                      # иначе из капитализации/цены
            mcap, price = latest("market_cap_mln"), latest("price_end")
            shares = mcap / price if (mcap and price and price > 0) else np.nan
        if not (shares and shares > 0):
            raise ValueError(f"{ticker}: не удалось определить кол-во акций")
        f = Fundamentals(
            revenue_ltm=rev,
            ebit_margin=ebit_margin,
            da_pct=(abs(latest("amortization_mln", 0.0)) / rev) or 0.06,
            capex_pct=capex_pct,
            nwc_pct=0.10,                                    # дефолт (NWC из панели шумит)
            tax_rate=latest("effective_tax_rate_filled", latest("statutory_tax_rate_filled", 0.20)),
            net_debt=latest("net_debt_mln", 0.0),
            total_debt=latest("total_debt_mln", None),
            shares=shares,
        )
        beta = beta if beta is not None else (latest("beta_imoex", 1.0) or 1.0)
        # историч. 3y CAGR выручки → forward-рост (клип)
        revs = sub["revenue_mln"].dropna().tail(4).values
        cagr = ((revs[-1] / revs[0]) ** (1 / (len(revs) - 1)) - 1) if len(revs) >= 2 and revs[0] > 0 else 0.05
        cagr = float(np.clip(cagr, 0.0, 0.15))
        dcf = cls(ticker, f, beta=beta, g=g, cost_of_debt=cost_of_debt, **kw)
        dcf.set_growth([cagr] * cls.YEARS)
        return dcf


# ════════════════════════════════════════════════════════════════════════
#  Пример использования
# ════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # ── РУЧНОЙ режим: моковый фундаментал Лукойла (млрд ₽; shares — млрд шт) ──
    lkoh = Fundamentals(
        revenue_ltm=8600.0, ebit_margin=0.13, da_pct=0.06, capex_pct=0.07, nwc_pct=0.10,
        tax_rate=0.25, net_debt=-1000.0, total_debt=400.0, shares=0.6926,
    )
    dcf = DCFValuation("LKOH", lkoh, beta=0.9, erp=0.10, cost_of_debt=0.16, g=0.02)
    dcf.set_growth([0.04, 0.04, 0.03, 0.03, 0.03], [0.13, 0.13, 0.13, 0.12, 0.12])
    dcf.report()

    base = dcf.wacc()
    sm = dcf.sensitivity_matrix(
        wacc_range=[base - 0.04, base - 0.02, base, base + 0.02, base + 0.04],
        g_range=[0.01, 0.015, 0.02, 0.025, 0.03])
    print("\n  Матрица чувствительности (Fair Price, ₽): строки WACC × столбцы g")
    print(sm.round(0).to_string())
