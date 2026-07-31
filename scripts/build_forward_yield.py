#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Форвардная дивдоходность (таблица Марламова, 2 года), макро-режим и лагированный
research backtest. Live-путь гоняется в update.yml после build_data. Реальные данные
ISS MOEX + site/data.json + историческая панель/adjusted prices. Без синтетики.

Математика (двухпериодная, очищенная база):
  net = 0.87 (НДФЛ 13%; у высоких доходов 15% — упрощение)
  Yield1 = div1*net / price
  P_adj  = price − div1*net            (база затрат снижается на первый ЧИСТЫЙ дивиденд)
  Yield2 = div2*net / P_adj            (доходность 2-го года от ОЧИЩЕННОЙ базы)
  Total2 = (div1+div2)*net / price     (сумма чистых дивидендов за 2 выплаты к исходной цене)
  ExpectedNetYield = P(payout) * div1 * net / price
  EntrySpread = ExpectedNetYield - RFR_after_tax. Entry threshold = +3 p.p.

div1 берём из модельного прогноза (data.json.dividend_forecast), div2 — из авторского
my_dividend_forecasts.json (модель на 2-й год не прогнозирует). Нет div2 → строка без Yield2.

Макро: RFR = 1-летняя точка G-кривой MOEX (zcyc.json yearyields). Режим = IMOEX vs SMA200 (доска SNDX).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from src.strategies.forward_repricing import build_backtest_from_files

DATA_JSON = os.path.join(REPO, "site", "data.json")
FORECASTS = os.path.join(REPO, "my_dividend_forecasts.json")
OUT = os.path.join(REPO, "site", "marlamov.json")
PANEL = os.path.join(REPO, "data", "panels_final", "panel_russia_final.csv")
ADJUSTED_PRICES = os.path.join(REPO, "data", "russian_stocks_adjusted.xlsx")
MCFTR = os.path.join(REPO, "data", "индекс_мосбиржи_полн_дох.csv")
ISS = "https://iss.moex.com/iss"
UA = {"User-Agent": "dividend-site/forward-yield (+github.com/eremkindv91)", "Accept": "application/json"}
NET = 0.87                              # 1 − НДФЛ 13%
ENTRY_THRESHOLD = 0.03                 # Expected net spread >= +3 p.p. → model eligibility


def http_json(url: str, retries: int = 4, timeout: int = 30) -> dict:
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status != 200:
                    raise urllib.error.HTTPError(url, r.status, "non-200", r.headers, None)
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 ** a)
    raise RuntimeError(f"ISS недоступен: {url} :: {last}")


def rows(block: dict) -> list:
    return block.get("data", [])


def rfr_1y() -> float | None:
    """1-летняя точка G-кривой MOEX (zero-rate, доли)."""
    try:
        d = http_json(f"{ISS}/engines/stock/zcyc.json?iss.meta=off")
        b = d["yearyields"]; ci = {c: i for i, c in enumerate(b["columns"])}
        pts = sorted((float(r[ci["period"]]), float(r[ci["value"]])) for r in rows(b))
        if not pts:
            return None
        # линейная интерполяция к t=1.0
        for i in range(1, len(pts)):
            if pts[i][0] >= 1.0:
                (t0, y0), (t1, y1) = pts[i - 1], pts[i]
                y = y0 + (y1 - y0) * (1.0 - t0) / (t1 - t0) if t1 != t0 else y0
                return y / 100.0
        return pts[-1][1] / 100.0
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[fwd] RFR из G-кривой не получен: {e}\n")
        return None


def macro_regime() -> dict:
    """IMOEX (доска SNDX) vs SMA200 → Risk-On/Off. ISS history пагинирует по 100 → тянем 3 страницы desc."""
    try:
        base = (f"{ISS}/history/engines/stock/markets/index/boards/SNDX/securities/IMOEX.json"
                "?iss.meta=off&history.columns=TRADEDATE,CLOSE&sort_order=desc")
        closes = []
        for start in (0, 100, 200):
            h = http_json(f"{base}&start={start}")["history"]
            page = [float(r[1]) for r in rows(h) if r[1] is not None]
            closes.extend(page)
            if len(page) < 100:
                break
        if len(closes) < 200:
            return {"regime": None, "imoex": None, "sma200": None}
        last = closes[0]                                # newest (sort desc)
        sma200 = sum(closes[:200]) / 200.0
        return {"regime": "Risk-On" if last >= sma200 else "Risk-Off",
                "imoex": round(last, 2), "sma200": round(sma200, 2)}
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[fwd] IMOEX/SMA200 не получены: {e}\n")
        return {"regime": None, "imoex": None, "sma200": None}


def load_universe() -> dict:
    """{ticker: {price, div1_model, name, sector, status, mcap, yield_expected}} из site/data.json."""
    with open(DATA_JSON, encoding="utf-8") as f:
        d = json.load(f)
    out = {}
    for t in d["tickers"]:
        out[t["ticker"]] = {
            "price": t.get("price"), "div1_model": t.get("dividend_forecast"),
            "name": t.get("name"), "sector": t.get("sector"), "status": t.get("status"),
            "mcap": t.get("mcap") or 0, "yield_expected": t.get("dividend_yield_expected"),
            "yield_if_paid": t.get("dividend_yield_if_paid"),
        }
    # price_asof наследуется в meta.source_as_of: forward-доходность — DERIVED-аналитика,
    # её фактическая дата = дата цен входа (data.json), а не время пересчёта модели.
    return out, d["meta"].get("rf_ofz"), d["meta"].get("price_asof")


def ensure_forecasts(uni: dict) -> dict:
    """Прочитать авторский файл прогнозов; если нет — создать шаблон из ok-тикеров (div2=null)."""
    if os.path.exists(FORECASTS):
        with open(FORECASTS, encoding="utf-8") as f:
            return json.load(f)
    tmpl = {}
    ok = sorted([k for k, v in uni.items() if v["status"] == "ok" and v["price"]],
                key=lambda k: -(uni[k]["mcap"] or 0))[:60]
    for k in ok:
        d1 = uni[k]["div1_model"]
        tmpl[k] = {"div_1": d1, "div_2": None, "forecast_status": "missing", "note": "Независимый прогноз Div2 отсутствует"}
    with open(FORECASTS, "w", encoding="utf-8") as f:
        json.dump(tmpl, f, ensure_ascii=False, indent=2)
    sys.stderr.write(f"[fwd] создан шаблон {FORECASTS} ({len(tmpl)} тикеров; div_2=заглушка, впиши свои)\n")
    return tmpl


def signal(spread: float | None, *, eligible: bool, forecast_valid: bool) -> str:
    if not forecast_valid:
        return "Недостаточно данных"
    if eligible:
        return "Выше модельного порога"
    if spread is not None and spread >= 0:
        return "Наблюдать"
    return "Ниже модельного порога"


def build_rows(uni: dict, fc: dict, rfr: float | None) -> list:
    out = []
    for tk, f in fc.items():
        u = uni.get(tk)
        if not u or not u["price"]:
            continue
        price = float(u["price"])
        div1 = f.get("div_1")
        if div1 in (None, ""):
            div1 = u["div1_model"]
        div1 = float(div1) if div1 not in (None, "") else None
        placeholder = bool(f.get("forecast_status") == "placeholder" or "заглушк" in str(f.get("note") or "").lower())
        div2 = None if placeholder else f.get("div_2")
        div2 = float(div2) if div2 not in (None, "") else None
        if not div1 or div1 <= 0:
            continue
        gross_yield1 = div1 / price
        gross_spread = (gross_yield1 - rfr) if rfr is not None else None
        yield_expected_pct = u.get("yield_expected")
        yield_if_paid_pct = u.get("yield_if_paid")
        payout_probability = None
        if isinstance(yield_expected_pct, (int, float)) and isinstance(yield_if_paid_pct, (int, float)) and yield_if_paid_pct > 0:
            payout_probability = min(1.0, max(0.0, float(yield_expected_pct) / float(yield_if_paid_pct)))
        expected_dividend = div1 * payout_probability if payout_probability is not None else None
        expected_net_yield = expected_dividend * NET / price if expected_dividend is not None else None
        matched_rfr = rfr * NET if rfr is not None else None
        expected_net_spread = expected_net_yield - matched_rfr if expected_net_yield is not None and matched_rfr is not None else None
        eligible = bool(expected_net_spread is not None and expected_net_spread >= ENTRY_THRESHOLD)
        yield1 = gross_yield1 * NET
        p_adj = price - div1 * NET
        # sanity: net-доходность >30% или очищенная база <50% цены = модельный артефакт
        # (пенни-стоки/шум прогноза) → исключаем, иначе Y2 взрывается на P_adj→0
        if yield1 > 0.30 or p_adj < 0.5 * price:
            continue
        yield2 = (div2 * NET / p_adj) if (div2 and div2 > 0 and p_adj > 0) else None
        total2 = ((div1 + div2) * NET / price) if div2 and div2 > 0 else None
        spread = (yield2 - rfr) if (yield2 is not None and rfr is not None) else None
        out.append({
            "ticker": tk, "name": u["name"], "sector": u["sector"],
            "price": round(price, 2), "div1": round(div1, 2), "div2": (round(div2, 2) if div2 else None),
            "gross_yield1": round(gross_yield1, 4),
            "gross_spread": (round(gross_spread, 4) if gross_spread is not None else None),
            "yield1": round(yield1, 4), "price_adj": round(p_adj, 2),
            "yield2": (round(yield2, 4) if yield2 is not None else None),
            "total2": (round(total2, 4) if total2 is not None else None),
            "spread": (round(spread, 4) if spread is not None else None),
            "payout_probability": (round(payout_probability, 4) if payout_probability is not None else None),
            "expected_dividend": (round(expected_dividend, 4) if expected_dividend is not None else None),
            "expected_net_yield": (round(expected_net_yield, 4) if expected_net_yield is not None else None),
            "matched_rfr": (round(matched_rfr, 4) if matched_rfr is not None else None),
            "expected_net_spread": (round(expected_net_spread, 4) if expected_net_spread is not None else None),
            "entry_threshold": ENTRY_THRESHOLD,
            "eligible": eligible,
            "forecast_status": "insufficient_forecast" if placeholder or div2 is None else "independent_forecast",
            "signal": signal(expected_net_spread, eligible=eligible, forecast_valid=not placeholder and div2 is not None),
            "note": "Независимый прогноз Div2 отсутствует" if placeholder else (f.get("note") or ""),
            "div2_missing": div2 is None,
        })
    # сортировка: сначала со spread (по убыванию), потом без div2
    out.sort(key=lambda r: (r["spread"] is None, -(r["spread"] or -9)))
    return out


def build_backtest() -> dict:
    """Build the deterministic lagged baseline, preserving last-good on local data failure."""
    try:
        return build_backtest_from_files(PANEL, ADJUSTED_PRICES, MCFTR)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[fwd] backtest не пересобран: {exc}\n")
        try:
            with open(OUT, encoding="utf-8") as f:
                previous = json.load(f).get("backtest")
            if isinstance(previous, dict) and previous.get("metrics"):
                previous = dict(previous)
                previous["last_good"] = True
                return previous
        except Exception:  # noqa: BLE001
            pass
        return {
            "status": "unavailable",
            "point_in_time": False,
            "reason": "Исторический research baseline временно недоступен; текущая таблица продолжает работать.",
        }


def main() -> int:
    if not os.path.exists(DATA_JSON):
        sys.stderr.write("[fwd] СТОП: нет site/data.json — сначала build_data.py\n")
        return 1
    uni, rf_ofz, price_asof = load_universe()
    fc = ensure_forecasts(uni)
    rfr = rfr_1y()
    if rfr is None:
        rfr = float(rf_ofz) if rf_ofz else None        # фолбэк на RFR из data.json
    if rfr is None or not (0.05 <= rfr <= 0.40):        # sanity (защита от 0%/500%)
        sys.stderr.write(f"[fwd] СТОП: RFR вне диапазона [5%,40%]: {rfr}\n")
        return 1
    macro = macro_regime()
    rws = build_rows(uni, fc, rfr)
    if not rws:
        sys.stderr.write("[fwd] СТОП: пустая таблица (нет прогнозов/цен)\n")
        return 1
    n_div2 = sum(1 for r in rws if not r["div2_missing"])
    backtest = build_backtest()
    payload = {
        "meta": {
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_as_of": price_asof,                 # дата цен входа (data.json), не время пересчёта
            "rfr": round(rfr, 4), "net_tax": NET,
            "regime": macro["regime"], "imoex": macro["imoex"], "sma200": macro["sma200"],
            "n": len(rws), "n_with_div2": n_div2,
            "entry_threshold": ENTRY_THRESHOLD,
            "eligible_count": sum(1 for row in rws if row["eligible"]),
            "note": "Eligibility использует ожидаемую чистую дивдоходность: P(выплата)×Div1×(1−НДФЛ)/Price "
                    "минус сопоставимая RFR после налога. Независимый Div2 показан только как сценарий и не "
                    "создаёт торговый статус. Не ИИР.",
        },
        "rows": rws,
        "backtest": backtest,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    backtest_months = (backtest.get("metrics") or {}).get("months", 0)
    sys.stderr.write(f"[fwd] OK → {OUT} (строк={len(rws)}, с div2={n_div2}, режим={macro['regime']}, "
                     f"RFR={rfr*100:.2f}%, backtest={backtest_months} мес.)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
