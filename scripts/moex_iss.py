#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Фетч цен с публичного MOEX ISS (без токена), борд TQBR.

Один bulk-запрос на весь борд (≈250 бумаг) вместо запроса на тикер — это минимизирует
число обращений и риск рейт-лимита/блокировки облачных IP. Поля цены по приоритету:
LCLOSEPRICE (закрытие последней сессии) → LAST (последняя сделка) → PREVPRICE (офиц.
закрытие пред. дня). Плюс SHORTNAME (название) для подписи на сайте.

Особенности:
  • только stdlib (urllib) — без внешних зависимостей и токенов;
  • retry + экспоненциальный backoff, User-Agent (пустой UA MOEX иногда режет);
  • кэш последних валидных цен (model_output/prices_cache.json): если ISS недоступен
    (например, 403 с облачного IP GitHub Actions) — используем кэш и помечаем как несвежие.

CLI:  python scripts/moex_iss.py SBER GAZP LKOH
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import trading_calendar as _tc  # торговый календарь MOEX (выходной ≠ торговая сессия)
except Exception:  # noqa: BLE001 — деградация к простой проверке будня
    _tc = None


def _is_trading_day(d: date) -> bool:
    return _tc.is_trading_day(d) if _tc is not None else d.weekday() < 5

ISS_BOARD_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json"
    "?iss.meta=off&iss.only=securities,marketdata"
    "&securities.columns=SECID,SHORTNAME,PREVPRICE,PREVDATE,LOTSIZE"
    "&marketdata.columns=SECID,LAST,LCLOSEPRICE,SYSTIME"
)
ISS_CANDLES_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{tk}/candles.json"
    "?iss.meta=off&interval={interval}&from={frm}&till={till}&candles.columns=close,begin,value"
)
ISS_DIV_URL = (
    "https://iss.moex.com/iss/securities/{tk}/dividends.json"
    "?iss.meta=off&dividends.columns=secid,isin,registryclosedate,value,currencyid"
)
ISS_OHLCV_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/boards/{board}/securities/{tk}/candles.json"
    "?iss.meta=off&interval={interval}&from={frm}&till={till}"
    "&candles.columns=begin,open,high,low,close,value,volume&start={start}"
)
USER_AGENT = "dividend-forecast-site/1.0 (+https://github.com/eremkindv91/dividend-factor-strategies)"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(REPO, "model_output", "prices_cache.json")


def _http_get_json(url: str, timeout: int = 25, retries: int = 4) -> dict:
    """GET JSON c retry + экспоненциальным backoff. Бросает исключение, если все попытки неуспешны."""
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                        "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise urllib.error.HTTPError(url, resp.status, "non-200", resp.headers, None)
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = 2 ** attempt
            sys.stderr.write(f"[moex_iss] попытка {attempt + 1}/{retries} не удалась: {e}; "
                             f"повтор через {wait}s\n")
            time.sleep(wait)
    raise RuntimeError(f"ISS недоступен после {retries} попыток: {last_err}")


def _rows(block: dict) -> List[dict]:
    cols = block["columns"]
    return [dict(zip(cols, r)) for r in block["data"]]


def fetch_board_prices(timeout: int = 25, retries: int = 4) -> Tuple[Dict[str, dict], Optional[str]]:
    """Вернуть ({SECID: {price, price_field, name, prev_date}}, systime) по всему борду TQBR.
    systime — серверное время ISS (МСК, 'YYYY-MM-DD HH:MM:SS'): нужно, чтобы честно датировать
    LCLOSEPRICE/LAST (сегодняшний close доступен только ПОСЛЕ закрытия основной сессии)."""
    payload = _http_get_json(ISS_BOARD_URL, timeout=timeout, retries=retries)
    sec = {r["SECID"]: r for r in _rows(payload["securities"])}
    mkt_rows = _rows(payload["marketdata"])
    mkt = {r["SECID"]: r for r in mkt_rows}
    systime = next((r.get("SYSTIME") for r in mkt_rows if r.get("SYSTIME")), None)
    out: Dict[str, dict] = {}
    for secid, s in sec.items():
        m = mkt.get(secid, {})
        price, field = None, None
        for cand, fld in ((m.get("LCLOSEPRICE"), "LCLOSEPRICE"),
                          (m.get("LAST"), "LAST"),
                          (s.get("PREVPRICE"), "PREVPRICE")):
            if cand is not None and float(cand) > 0:
                price, field = float(cand), fld
                break
        out[secid] = {
            "price": price,
            "price_field": field,
            "name": s.get("SHORTNAME"),
            "prev_date": s.get("PREVDATE"),
            "lot_size": int(s.get("LOTSIZE") or 1),
        }
    return out, systime


def _session_close_date(systime: Optional[str]) -> Optional[str]:
    """Дата сегодняшнего закрытия, если основная сессия TQBR (~18:50 МСК) уже закрылась; иначе None.
    Тогда LCLOSEPRICE/LAST = сегодняшний close. До закрытия они отражают ПРЕДЫДУЩУЮ сессию (=PREVDATE)."""
    if not systime:
        return None
    try:
        dt = datetime.strptime(systime, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    # «сегодняшний close» существует ТОЛЬКО в торговый день после закрытия сессии.
    # В выходной/праздник SYSTIME=сейчас, но сессии не было → дата close отсутствует
    # (цена = PREVPRICE за последний торговый день, см. _price_asof).
    if not _is_trading_day(dt.date()):
        return None
    return dt.date().isoformat() if dt.hour >= 19 else None


def _price_asof(field: Optional[str], prev_date: Optional[str], close_date: Optional[str]) -> Optional[str]:
    """Честная дата цены: PREVPRICE → PREVDATE; LCLOSEPRICE/LAST → сегодня только если сессия закрыта."""
    if field == "PREVPRICE":
        return prev_date
    return close_date or prev_date


def _f(x):
    try:
        return float(x) if x is not None else None
    except (ValueError, TypeError):
        return None


def fetch_ohlcv(ticker: str, frm: str, till: str, board: str = "TQBR", interval: int = 24,
                timeout: int = 25, retries: int = 3) -> List[dict]:
    """Дневные свечи OHLCV на доске board за [frm, till]. Возвращает list[dict]
    {trade_date, open, high, low, close, value, volume} по возрастанию даты (дедуп по дате).
    Пагинация ISS по 500 строк (многолетняя дневная история > одной страницы).
    Число сделок ISS в свечах НЕ отдаёт — поля нет (не выдумываем). RuntimeError при сбое сети."""
    rows_out: Dict[str, dict] = {}
    start = 0
    while True:
        url = ISS_OHLCV_URL.format(board=board, tk=ticker, interval=interval, frm=frm, till=till, start=start)
        payload = _http_get_json(url, timeout=timeout, retries=retries)
        batch = _rows(payload.get("candles", {"columns": [], "data": []}))
        if not batch:
            break
        for r in batch:
            c = r.get("close")
            d = str(r.get("begin"))[:10]
            if c is None or _f(c) is None or _f(c) <= 0 or len(d) != 10:
                continue
            rows_out[d] = {"trade_date": d, "open": _f(r.get("open")), "high": _f(r.get("high")),
                           "low": _f(r.get("low")), "close": float(c),
                           "value": _f(r.get("value")), "volume": _f(r.get("volume"))}
        if len(batch) < 500:
            break
        start += len(batch)
    return [rows_out[d] for d in sorted(rows_out)]


def fetch_candles(ticker: str, days: int = 430, interval: int = 24, timeout: int = 25,
                  retries: int = 3) -> List[Tuple[str, float]]:
    """Свечи (close, дата) за последние ~days календарных дней, борд TQBR. interval: 24=дневные,
    31=месячные. Возвращает [(YYYY-MM-DD, close)] по возрастанию; держим число строк < лимита
    пагинации MOEX 500 (месячные за 8 лет ≈96, дневные за 430д ≈280). RuntimeError при сбое."""
    till = date.today().isoformat()
    frm = (date.today() - timedelta(days=days)).isoformat()
    url = ISS_CANDLES_URL.format(tk=ticker, interval=interval, frm=frm, till=till)
    payload = _http_get_json(url, timeout=timeout, retries=retries)
    out = []
    for r in _rows(payload.get("candles", {"columns": [], "data": []})):
        c = r.get("close")
        if c is not None and float(c) > 0:
            v = r.get("value")
            out.append((str(r.get("begin"))[:10], float(c), float(v) if v is not None else 0.0))   # (дата, close, оборот ₽)
    out.sort()
    return out


def fetch_dividend_records(ticker: str, timeout: int = 25, retries: int = 3) -> List[dict]:
    """Official dividend rows with security identifiers and record dates."""
    payload = _http_get_json(ISS_DIV_URL.format(tk=ticker), timeout=timeout, retries=retries)
    out = []
    for row in _rows(payload.get("dividends", {"columns": [], "data": []})):
        value, currency, record_date = row.get("value"), row.get("currencyid"), row.get("registryclosedate")
        if value is not None and float(value) > 0 and currency in (None, "SUR", "RUB") and record_date:
            out.append({
                "secid": row.get("secid") or ticker,
                "isin": row.get("isin"),
                "registryclosedate": str(record_date)[:10],
                "value": float(value),
                "currencyid": "RUB" if currency in (None, "SUR") else currency,
            })
    out.sort(key=lambda row: (row["registryclosedate"], row["value"]))
    return out


def fetch_dividends(ticker: str, timeout: int = 25, retries: int = 3) -> List[Tuple[str, float]]:
    """История выплат: [(дата отсечки YYYY-MM-DD, дивиденд на акцию ₽)], только рубли (SUR).
    Бесплатный публичный endpoint MOEX ISS. RuntimeError при недоступности."""
    return [(row["registryclosedate"], row["value"])
            for row in fetch_dividend_records(ticker, timeout=timeout, retries=retries)]


def _load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


def get_prices(tickers: List[str], allow_cache: bool = True) -> dict:
    """
    Цены по списку тикеров. Возвращает:
      { "prices": {ticker: {price, name, price_field, fresh: bool, asof}},
        "meta": {source_ok, price_asof, n_fresh, n_cached, n_missing, fetched_at} }
    Стратегия: один bulk-запрос; при сбое — кэш (fresh=False). Тикеры без цены и без
    кэша остаются с price=None (инференс пометит «нет данных»).
    """
    cache = _load_cache()
    fresh_prices: Dict[str, dict] = {}
    source_ok = True
    try:
        board, systime = fetch_board_prices()
        close_date = _session_close_date(systime)     # дата сегодняшнего close или None (до закрытия)
        for tk in tickers:
            row = board.get(tk)
            if row and row["price"] is not None:
                asof = _price_asof(row["price_field"], row.get("prev_date"), close_date)
                fresh_prices[tk] = {"price": row["price"], "name": row["name"],
                                    "price_field": row["price_field"], "asof": asof,
                                    "lot_size": row.get("lot_size") or 1}
    except Exception as e:  # noqa: BLE001
        source_ok = False
        sys.stderr.write(f"[moex_iss] ИСТОЧНИК НЕДОСТУПЕН: {e}\n")

    # обновляем кэш свежими ценами
    for tk, v in fresh_prices.items():
        cache[tk] = v
    if fresh_prices:
        _save_cache(cache)

    prices, n_fresh, n_cached, n_missing = {}, 0, 0, 0
    for tk in tickers:
        if tk in fresh_prices:
            prices[tk] = {**fresh_prices[tk], "fresh": True}
            n_fresh += 1
        elif allow_cache and tk in cache and cache[tk].get("price") is not None:
            prices[tk] = {**cache[tk], "fresh": False}
            n_cached += 1
        else:
            prices[tk] = {"price": None, "name": cache.get(tk, {}).get("name"),
                          "price_field": None, "asof": None, "fresh": False,
                          "lot_size": cache.get(tk, {}).get("lot_size") or 1}
            n_missing += 1

    # price_asof = реальная торговая дата данных (максимум по фактически использованным),
    # а НЕ дата запуска скрипта. Свежие — по их asof; иначе — самая свежая дата из кэша.
    fresh_asofs = [v.get("asof") for v in fresh_prices.values() if v.get("asof")]
    cache_asofs = [v.get("asof") for v in cache.values() if v.get("asof")]
    meta = {
        "source_ok": source_ok,
        "price_asof": (max(fresh_asofs) if fresh_asofs else (max(cache_asofs) if cache_asofs else None)),
        "n_fresh": n_fresh, "n_cached": n_cached, "n_missing": n_missing,
        "fetched_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    return {"prices": prices, "meta": meta}


def _cli() -> int:
    tickers = sys.argv[1:] or ["SBER", "GAZP", "LKOH"]
    res = get_prices(tickers)
    print("META:", json.dumps(res["meta"], ensure_ascii=False))
    for tk in tickers:
        p = res["prices"][tk]
        print(f"  {tk:6s} {p['price']!s:>10}  [{p['price_field']}] "
              f"fresh={p['fresh']}  {p['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
