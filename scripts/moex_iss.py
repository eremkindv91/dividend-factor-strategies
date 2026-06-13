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
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

ISS_BOARD_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json"
    "?iss.meta=off&iss.only=securities,marketdata"
    "&securities.columns=SECID,SHORTNAME,PREVPRICE,PREVDATE"
    "&marketdata.columns=SECID,LAST,LCLOSEPRICE"
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


def fetch_board_prices(timeout: int = 25, retries: int = 4) -> Dict[str, dict]:
    """Вернуть {SECID: {price, price_field, name, prev_date}} по всему борду TQBR."""
    payload = _http_get_json(ISS_BOARD_URL, timeout=timeout, retries=retries)
    sec = {r["SECID"]: r for r in _rows(payload["securities"])}
    mkt = {r["SECID"]: r for r in _rows(payload["marketdata"])}
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
        }
    return out


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
    today = date.today().isoformat()
    cache = _load_cache()
    fresh_prices: Dict[str, dict] = {}
    source_ok = True
    try:
        board = fetch_board_prices()
        for tk in tickers:
            row = board.get(tk)
            if row and row["price"] is not None:
                fresh_prices[tk] = {"price": row["price"], "name": row["name"],
                                    "price_field": row["price_field"], "asof": today}
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
                          "price_field": None, "asof": None, "fresh": False}
            n_missing += 1

    meta = {
        "source_ok": source_ok,
        "price_asof": today if n_fresh else (max(
            [v.get("asof") for v in cache.values() if v.get("asof")], default=None)),
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
