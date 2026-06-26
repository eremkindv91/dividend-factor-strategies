#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единственный источник данных «Пилы рынка»: индекс полной доходности МосБиржи MCFTR.

Только stdlib (urllib) — этот модуль переиспользуется И в оффлайн-research (STAGE 1), И в
CI live-пути (production/build_marketsaw.py). Никаких pandas/numpy здесь.

ФАКТ-КОРРЕКТИРОВКА: MCFTR торгуется на доске RTSI (market=index, engine=stock), а НЕ на SNDX
(доска SNDX по MCFTR отдаёт 0 строк). Модуль авто-открывает primary-board через определение
бумаги и падает на RTSI как fallback, чтобы пережить переезд индекса.

ISS history (без токена): пагинация start=0,100,200,... Колонки берём TRADEDATE, CLOSE.
Валидация формы (монотонность/дыры/CLOSE>0) — на стороне STAGE 1, тут только сырые данные.

CLI:  python market_saw/shared/mcftr_fetch.py        # печатает диапазон и хвост
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

SECID = "MCFTR"
USER_AGENT = "dividend-forecast-site/marketsaw (+https://github.com/eremkindv91/dividend-factor-strategies)"
ISS = "https://iss.moex.com/iss"
FALLBACK_BOARD = ("stock", "index", "RTSI")  # engine, market, board
PAGE = 100


def _http_get_json(url: str, timeout: int = 25, retries: int = 4) -> dict:
    """GET JSON с retry + экспоненциальным backoff (паттерн scripts/moex_iss.py)."""
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise urllib.error.HTTPError(url, resp.status, "non-200", resp.headers, None)
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = 2 ** attempt
            sys.stderr.write(
                f"[mcftr_fetch] попытка {attempt + 1}/{retries} не удалась: {e}; повтор через {wait}s\n"
            )
            time.sleep(wait)
    raise RuntimeError(f"ISS недоступен после {retries} попыток: {last_err}")


def discover_board() -> Tuple[str, str, str]:
    """Вернуть (engine, market, board) primary-доски для MCFTR. Fallback — RTSI/index/stock."""
    try:
        d = _http_get_json(f"{ISS}/securities/{SECID}.json?iss.only=boards")
        b = d["boards"]
        ci = {c: i for i, c in enumerate(b["columns"])}
        rows = b["data"]
        # сначала primary, иначе любой с непустой историей
        primary = [r for r in rows if ci.get("is_primary") is not None and r[ci["is_primary"]] == 1]
        pick = (primary or rows)[0]
        eng = pick[ci["engine"]] or FALLBACK_BOARD[0]
        mkt = pick[ci["market"]] or FALLBACK_BOARD[1]
        brd = pick[ci["boardid"]] or FALLBACK_BOARD[2]
        return eng, mkt, brd
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[mcftr_fetch] board-discovery упал ({e}); fallback {FALLBACK_BOARD}\n")
        return FALLBACK_BOARD


def fetch_mcftr_history(
    engine: Optional[str] = None,
    market: Optional[str] = None,
    board: Optional[str] = None,
) -> List[Tuple[str, float]]:
    """
    Скачать всю историю MCFTR постранично. Вернуть список (TRADEDATE 'YYYY-MM-DD', CLOSE float),
    отсортированный по дате, без строк с пустым/неположительным CLOSE. Дедуп по дате — на STAGE 1.
    """
    if not (engine and market and board):
        engine, market, board = discover_board()
    base = (
        f"{ISS}/history/engines/{engine}/markets/{market}/boards/{board}/securities/{SECID}.json"
        "?iss.meta=off&history.columns=TRADEDATE,CLOSE"
    )
    out: List[Tuple[str, float]] = []
    start = 0
    while True:
        d = _http_get_json(f"{base}&start={start}")
        h = d["history"]
        ci = {c: i for i, c in enumerate(h["columns"])}
        rows = h["data"]
        if not rows:
            break
        for r in rows:
            dt = r[ci["TRADEDATE"]]
            cl = r[ci["CLOSE"]]
            if dt and cl is not None and float(cl) > 0:
                out.append((dt, float(cl)))
        if len(rows) < PAGE:
            break
        start += PAGE
    out.sort(key=lambda x: x[0])
    return out


if __name__ == "__main__":
    eng, mkt, brd = discover_board()
    sys.stderr.write(f"[mcftr_fetch] board: engine={eng} market={mkt} board={brd}\n")
    hist = fetch_mcftr_history(eng, mkt, brd)
    print(f"rows={len(hist)}")
    if hist:
        print(f"first={hist[0][0]} close={hist[0][1]}")
        print(f"last ={hist[-1][0]} close={hist[-1][1]}")
        for dt, cl in hist[-3:]:
            print(f"  {dt}  {cl}")
