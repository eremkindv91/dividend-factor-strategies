#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Data-level очистка портфельных данных (пост-шаг после build_data.py / build_momentum.py).

Чинит проблемы В ИСТОЧНИКЕ, чтобы мусор не попадал ни в один блок сайта (портфель, дашборды):

1) Дивиденды после сплита. div_forecast/current_dps приводятся к текущей базе акций по официальному
   реестру сплитов MOEX (T 1:10 → ÷10). Если после корректировки доходность всё равно абсурдна
   (dps/price > 35%), поля дивидендов зануляются с `dividend_status = split_unadjusted_dividend`.

2) Бумаги вне ML-пайплайна (data/supplementary_universe.json: SNGS/SNGSP, паи БПИФ,
   недавно размещённые) добавляются в data.json c реальной ценой MOEX, типом инструмента
   из ISS discovery и честным `status = no_model_coverage` — бумага выбираема и оценивается
   по рынку, но модельных полей (прогноз дивиденда, риск невыплаты, вердикт) у неё нет.
   Наличие истории для риск-метрик пишется ФАКТОМ в `risk_history` (напр. "90m" или "none"),
   а не предполагается: универсум истории шире ML-универсума.

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
# Бумаги вне ML-пайплайна, которые пользователь может держать. Раньше это был
# захардкоженный список из ДВУХ тикеров (SNGS/SNGSP), поэтому паи БПИФ и недавно
# размещённые бумаги получали ряд доходностей (универсум истории их уже знает), но
# НЕ получали строку в data.json — то есть не имели рыночной цены, названия и типа,
# и позиция оценивалась по цене покупки вместо рынка.
# Теперь источник один и тот же для истории и для цен — data/supplementary_universe.json.
SUPPLEMENT = os.path.join(os.path.dirname(SITE), "data", "supplementary_universe.json")
# Тип инструмента → отраслевая подпись. Фонду НЕЛЬЗЯ присваивать отрасль эмитента:
# пай — это корзина, а не компания, и сектор был бы выдумкой (сектор-кап к нему тоже
# не применяется без look-through состава).
TYPE_SECTOR = {"fund": "Биржевой фонд", "equity_preferred": None, "equity_ordinary": None}


def supplementary_instruments() -> list[tuple[str, str, str, str]]:
    """(secid, название, сектор, тип) для бумаг вне ML-универсума.

    Название и тип берём из ISS discovery (moex_instruments), а не придумываем.
    Если discovery недоступен — возвращаем то, что знаем из файла, чтобы цена всё
    равно подтянулась: без названия строка полезнее, чем отсутствие строки.
    """
    try:
        with open(SUPPLEMENT, encoding="utf-8") as fh:
            secids = [str(x["secid"]).upper() for x in json.load(fh).get("instruments", [])
                      if x.get("secid")]
    except (OSError, ValueError, KeyError) as e:
        log(f"дополнительный универсум не прочитан ({e}) — только ML-список")
        return []
    if not secids:
        return []
    described = {}
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import moex_instruments as mi  # noqa: E402
        described = mi.describe_many(secids)
    except Exception as e:  # noqa: BLE001
        log(f"discovery недоступен ({e}) — имена и типы будут пустыми")
    out = []
    for tk in secids:
        info = described.get(tk) or {}
        if info.get("found") and not info.get("is_traded"):
            log(f"{tk}: торги прекращены — строка в data.json не создаётся")
            continue
        itype = info.get("instrument_type") or "other"
        out.append((tk, info.get("short_name") or info.get("name") or tk,
                    TYPE_SECTOR.get(itype) or "нет отраслевой привязки", itype))
    return out


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


def risk_history_for(tk: str) -> str:
    """Есть ли у бумаги ряд доходностей — по факту, а не по предположению.

    Раньше здесь жёстко стояло "none": заглушки добавлялись до того, как универсум
    истории научился выходить за пределы ML-артефакта. После supplementary_universe.json
    у SNGS/SNGSP появилось 90 месячных наблюдений, и "none" стало прямой неправдой —
    фронт читает returns.json и считает по ним риск-метрики.
    """
    try:
        with open(os.path.join(SITE, "returns.json"), encoding="utf-8") as fh:
            row = (json.load(fh).get("data") or {}).get(tk)
    except (OSError, ValueError):
        return "unknown"
    if not row:
        return "none"
    n = sum(1 for x in row if isinstance(x, (int, float)))
    return f"{n}m" if n else "none"


def add_missing(data: dict) -> int:
    tickers = data["tickers"]; have = {t["ticker"] for t in tickers}
    added = 0
    for tk, name, sector, itype in supplementary_instruments():
        if tk in have:
            # бумага уже в файле — обновляем ФАКТ о наличии истории. Без этого поле
            # навсегда оставалось бы "none" с первого прогона, хотя ряд уже появился.
            for row in tickers:
                if row.get("ticker") == tk:
                    fresh = risk_history_for(tk)
                    if row.get("risk_history") != fresh:
                        row["risk_history"] = fresh
                        log(f"{tk}: risk_history → {fresh}")
                    # тип инструмента тоже мог появиться позже (discovery добавили после
                    # первой записи строки) — доносим, иначе фронт не отличит пай от акции
                    if row.get("instrument_type") != itype:
                        row["instrument_type"] = itype
                        row["sector_applicable"] = itype != "fund"
                        if itype == "fund" and TYPE_SECTOR.get("fund"):
                            row["sector"] = TYPE_SECTOR["fund"]
                        log(f"{tk}: instrument_type → {itype}")
                    break
            continue
        price = fetch_price(tk)
        tickers.append({
            "ticker": tk, "name": name, "sector": sector,
            "price": round(float(price), 2) if isinstance(price, (int, float)) else None,
            "price_field": "LAST" if price else None, "price_fresh": bool(price),
            "status": "no_model_coverage", "risk_history": risk_history_for(tk),
            "instrument_type": itype,   # equity_ordinary | equity_preferred | fund | other
            "sector_applicable": itype != "fund",   # к фонду сектор-кап без look-through не применяем
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
        price_jump = False
        for i in range(len(pr)):
            d = dv[i] if i < len(dv) else None
            # невозможный месячный дивиденд (до-сплитный дивиденд ÷ пост-сплитную цену и т.п.)
            # → клип к 0. НЕ повод исключать бумагу: ценовой ряд цел, риск-метрики считаются по нему.
            if isinstance(d, (int, float)) and abs(d) > DIV_MONTH_ABSURD:
                dv[i] = 0.0; clipped += 1
            # ТОЛЬКО ценовой разрыв (|месячный ценовой ретёрн| > порога) нельзя починить на уровне
            # returns → помечаем needs_adjustment и исключаем из риск-метрик. Дивспайк сюда не входит.
            if isinstance(pr[i], (int, float)) and abs(pr[i]) > SERIES_JUMP:
                price_jump = True
        if price_jump:
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
