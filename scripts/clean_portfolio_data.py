#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Data-level очистка портфельных данных (пост-шаг после build_data.py / build_momentum.py).

Чинит проблемы В ИСТОЧНИКЕ, чтобы мусор не попадал ни в один блок сайта (портфель, дашборды):

1) Дивиденды после сплита. div_forecast/current_dps приводятся к текущей базе акций по официальному
   реестру сплитов MOEX (T 1:10 → ÷10). Если после корректировки доходность всё равно абсурдна
   (dps/price > 35%), поля дивидендов зануляются с `dividend_status = split_unadjusted_dividend`.

2) SNGS / SNGSP (нет в ML-пайплайне) добавляются в data.json c реальной ценой MOEX и честным
   `status = no_model_coverage` (без модельных полей) — бумага выбираема и имеет цену, но помечена
   как «нет модельного покрытия / нет чистой истории для риска».

3) Split-like ряды returns.json (напр. TRNFP: дивспайк +1174%, GMKN и др.) помечаются
   `meta.series_status[тикер] = needs_adjustment`, а невозможные дивидендные всплески (>50%/мес)
   клиппятся к 0 — все блоки одинаково понимают причину исключения из риск-метрик.

Чистый stdlib. Идемпотентно. CLI: python scripts/clean_portfolio_data.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site")
ISS = "https://iss.moex.com/iss"
UA = {"User-Agent": "dividend-site/clean-portfolio", "Accept": "application/json"}
DIV_YIELD_ABSURD = 0.35        # dps/price выше → почти наверняка до-сплитный/битый дивиденд
DIV_MONTH_ABSURD = 0.50        # месячная дивдоходность выше → невозможна (клип к 0)
SERIES_JUMP = 2.5              # |месячный total-return| выше → split-like разрыв
# бумаги вне ML-пайплайна, которые пользователь может держать
MISSING = [("SNGS", "Сургутнефтегаз", "Нефть и газ"), ("SNGSP", "Сургутнефтегаз ап", "Нефть и газ")]


def log(m): sys.stderr.write(f"[clean] {m}\n")


def http_json(url: str, retries: int = 4):
    last = None
    for a in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e; time.sleep(1.5 * (a + 1))
    log(f"MOEX недоступен: {str(last)[:60]} — {url[:70]}")
    return None


def fetch_splits() -> dict:
    d = http_json(f"{ISS}/statistics/engines/stock/splits.json?iss.meta=off")
    out: dict[str, list] = {}
    if not d:
        return out
    b = d.get("splits") or {}
    ci = {c: i for i, c in enumerate(b.get("columns") or [])}
    for r in b.get("data") or []:
        out.setdefault(r[ci["secid"]], []).append({"before": r[ci["before"]], "after": r[ci["after"]], "date": r[ci["tradedate"]]})
    return out


def fetch_price(secid: str):
    d = http_json(f"{ISS}/engines/stock/markets/shares/boards/TQBR/securities/{secid}.json"
                  f"?iss.meta=off&iss.only=securities,marketdata")
    if not d:
        return None
    def block(name):
        blk = d.get(name) or {}; cols = blk.get("columns") or []; rows = blk.get("data") or []
        return dict(zip(cols, rows[0])) if rows else {}
    md, sec = block("marketdata"), block("securities")
    return md.get("LAST") or md.get("MARKETPRICE") or sec.get("PREVPRICE")


def clean_dividends(tickers: list, splits: dict) -> int:
    """Трогаем ТОЛЬКО дивиденды с абсурдной текущей доходностью (>35%). data.json актуален — старые
    сплиты (Транснефть 100:1 2024) в нём уже отражены, доходность нормальная (TRNFP 9.5%) → не трогаем.
    Только свежий до-сплитный мусор (T 1:10 2026: 45%) вправляем по реестру; не вправился → null."""
    fixed = 0
    for t in tickers:
        tk = t.get("ticker"); price = t.get("price")
        dps = t.get("dividend_forecast")
        if not (isinstance(dps, (int, float)) and isinstance(price, (int, float)) and price):
            continue
        # хирургично: трогаем ТОЛЬКО бумаги со сплитом в реестре MOEX (иначе копеечные акции с
        # завышенной точностью цены ложно занулялись бы). Нет сплита → не наша проблема, не трогаем.
        factor = 1.0
        for s in splits.get(tk, []):
            if s.get("before") and s.get("after"):
                factor *= s["before"] / s["after"]
        if factor == 1.0:
            continue
        if dps / price <= DIV_YIELD_ABSURD:            # доходность уже нормальная (сплит стар, отражён) → не трогаем
            continue
        if (dps * factor) / price <= DIV_YIELD_ABSURD:   # свежий сплит объясняет и вправляет абсурд (T 1:10)
            for k in ("dividend_forecast", "dividend_forecast_lo", "dividend_forecast_hi", "current_dps"):
                if isinstance(t.get(k), (int, float)):
                    t[k] = round(t[k] * factor, 4)
            t["dividend_yield_expected"] = round(100 * t["dividend_forecast"] / price, 2)
            t["dividend_status"] = "split_adjusted"
            fixed += 1
        else:                                          # не объяснимо сплитом → зануляем с причиной
            for k in ("dividend_forecast", "dividend_forecast_lo", "dividend_forecast_hi",
                      "current_dps", "dividend_yield_expected", "dividend_yield_if_paid", "payout"):
                if k in t:
                    t[k] = None
            t["dividend_status"] = "split_unadjusted_dividend"
            t["forecast_note"] = "Дивиденд в источнике выглядит до-сплитным/битым — обнулён до проверки"
            fixed += 1
    return fixed


def add_missing(data: dict) -> int:
    tickers = data["tickers"]; have = {t["ticker"] for t in tickers}
    added = 0
    for tk, name, sector in MISSING:
        if tk in have:
            continue
        price = fetch_price(tk)
        tickers.append({
            "ticker": tk, "name": name, "sector": sector,
            "price": round(float(price), 2) if isinstance(price, (int, float)) else None,
            "price_field": "LAST" if price else None, "price_fresh": bool(price),
            "status": "no_model_coverage", "risk_history": "none",
            "dividend_forecast": None, "dividend_yield_expected": None, "cut_risk": None,
            "quality_barra": None, "stability_score": None, "mom_score": None, "vol_ann": None,
            "payout": None, "nd_ebitda": None, "shap_top5": None, "valuation": None,
            "verdict": None, "verdict_score": None, "dividend_yield_if_paid": None, "flags": ["no_model_coverage"],
            "forecast_note": "Нет в ML-пайплайне проекта: цена — рынок MOEX, модельные метрики и чистая история недоступны",
        })
        added += 1
        log(f"добавлен {tk} (цена {price}) как no_model_coverage")
    return added


def clean_returns() -> dict:
    p = os.path.join(SITE, "returns.json")
    if not os.path.exists(p):
        return {"anomalies": 0}
    ret = json.load(open(p, encoding="utf-8"))
    data, div = ret.get("data") or {}, ret.get("div") or {}
    status = {}
    clipped = 0
    for tk, pr in data.items():
        dv = div.get(tk) or []
        anomaly = False
        for i in range(len(pr)):
            d = dv[i] if i < len(dv) else None
            # невозможный месячный дивиденд → клип к 0 (корректировка ряда)
            if isinstance(d, (int, float)) and abs(d) > DIV_MONTH_ABSURD:
                dv[i] = 0.0; clipped += 1; anomaly = True
            tot = (pr[i] if isinstance(pr[i], (int, float)) else 0) + (dv[i] if i < len(dv) and isinstance(dv[i], (int, float)) else 0)
            if abs(tot) > SERIES_JUMP:
                anomaly = True
        if anomaly:
            status[tk] = "needs_adjustment"
    ret.setdefault("meta", {})["series_status"] = status
    with open(p, "w", encoding="utf-8") as f:
        json.dump(ret, f, ensure_ascii=False, separators=(",", ":"))
    log(f"returns.json: {len(status)} рядов помечены needs_adjustment, {clipped} дивспайков клиппнуто")
    return {"anomalies": len(status), "clipped": clipped, "tickers": sorted(status)}


def main() -> int:
    p = os.path.join(SITE, "data.json")
    if not os.path.exists(p):
        log("data.json отсутствует — пропуск"); return 0
    data = json.load(open(p, encoding="utf-8"))
    splits = fetch_splits()
    fixed = clean_dividends(data["tickers"], splits)
    added = add_missing(data)
    if added:
        m = data.setdefault("meta", {})
        m["n_total"] = len(data["tickers"])
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    log(f"data.json: дивидендов исправлено {fixed}, добавлено бумаг {added}")
    rr = clean_returns()
    log(f"OK: needs_adjustment рядов {rr.get('anomalies')} ({', '.join(rr.get('tickers', [])[:8])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
