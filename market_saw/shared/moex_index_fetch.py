#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Универсальный загрузчик индексов MOEX ISS для двухконтурной «Пилы» (MCFTR и IMOEX).

Обобщение mcftr_fetch.py на любой secid. Только stdlib (urllib) — работает и оффлайн,
и в CI. Разделяет ЗАКРЫТЫЙ ряд (history, завершённые сессии) и LIVE snapshot (marketdata,
внутридневная котировка). Live snapshot НИКОГДА не подтверждает экстремумы — это делает
только официальный CLOSE (см. imoex_engine / build_*).

CLI:  python market_saw/shared/moex_index_fetch.py IMOEX
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

USER_AGENT = "dividend-forecast-site/marketsaw (+https://github.com/eremkindv91/dividend-factor-strategies)"
ISS = "https://iss.moex.com/iss"
PAGE = 100

# запасная доска на случай сбоя discover: (engine, market, board)
FALLBACK_BOARDS = {"MCFTR": ("stock", "index", "RTSI"), "IMOEX": ("stock", "index", "SNDX")}


def _http_get_json(url: str, timeout: int = 25, retries: int = 4) -> dict:
    """GET JSON с retry + экспоненциальным backoff; проверка HTTP-статуса."""
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise urllib.error.HTTPError(url, resp.status, "non-200", resp.headers, None)
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = 2 ** attempt
            sys.stderr.write(f"[moex_index_fetch] попытка {attempt + 1}/{retries} не удалась: {e}; повтор {wait}s\n")
            time.sleep(wait)
    raise RuntimeError(f"ISS недоступен после {retries} попыток: {last_err}")


def discover_index_board(secid: str) -> Tuple[str, str, str]:
    """(engine, market, board) primary-доски индекса; fallback — из FALLBACK_BOARDS."""
    fb = FALLBACK_BOARDS.get(secid.upper(), ("stock", "index", "SNDX"))
    try:
        d = _http_get_json(f"{ISS}/securities/{secid}.json?iss.only=boards")
        b = d["boards"]
        ci = {c: i for i, c in enumerate(b["columns"])}
        rows = b["data"]
        primary = [r for r in rows if ci.get("is_primary") is not None and r[ci["is_primary"]] == 1]
        pick = (primary or rows)[0]
        return (pick[ci["engine"]] or fb[0], pick[ci["market"]] or fb[1], pick[ci["boardid"]] or fb[2])
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[moex_index_fetch] board-discovery {secid} упал ({e}); fallback {fb}\n")
        return fb


def fetch_index_history(secid: str, engine=None, market=None, board=None) -> List[Tuple[str, float]]:
    """Вся история завершённых дневных CLOSE: [(TRADEDATE, CLOSE)], сортировка по дате, CLOSE>0.
    Дедупликация дат — на стороне билдера."""
    if not (engine and market and board):
        engine, market, board = discover_index_board(secid)
    base = (f"{ISS}/history/engines/{engine}/markets/{market}/boards/{board}/securities/{secid}.json"
            "?iss.meta=off&history.columns=TRADEDATE,CLOSE")
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
            dt, cl = r[ci["TRADEDATE"]], r[ci["CLOSE"]]
            if dt and cl is not None and float(cl) > 0:
                out.append((dt, float(cl)))
        if len(rows) < PAGE:
            break
        start += PAGE
    out.sort(key=lambda x: x[0])
    return out


def fetch_index_snapshot(secid: str, engine=None, market=None, board=None) -> Optional[dict]:
    """Внутридневной live snapshot из marketdata: {price, last_official, systime}. None при сбое.
    CURRENTVALUE — текущее значение сессии; LASTVALUE — предыдущий официальный CLOSE. Никогда не
    используется для подтверждения экстремумов (только текущая позиция маркера)."""
    if not (engine and market and board):
        engine, market, board = discover_index_board(secid)
    url = (f"{ISS}/engines/{engine}/markets/{market}/boards/{board}/securities/{secid}.json"
           "?iss.meta=off&iss.only=marketdata&marketdata.columns=SECID,CURRENTVALUE,LASTVALUE,SYSTIME,UPDATETIME")
    try:
        d = _http_get_json(url)
        md = d["marketdata"]
        ci = {c: i for i, c in enumerate(md["columns"])}
        if not md["data"]:
            return None
        r = md["data"][0]
        cur = r[ci.get("CURRENTVALUE")] if ci.get("CURRENTVALUE") is not None else None
        lastv = r[ci.get("LASTVALUE")] if ci.get("LASTVALUE") is not None else None
        price = cur if (cur is not None and float(cur) > 0) else lastv
        if price is None or not (float(price) > 0):
            return None
        return {"price": float(price), "last_official": (float(lastv) if lastv else None),
                "systime": r[ci.get("SYSTIME")] if ci.get("SYSTIME") is not None else None,
                "is_intraday": bool(cur is not None and float(cur) > 0)}
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[moex_index_fetch] snapshot {secid} упал ({e})\n")
        return None


if __name__ == "__main__":
    sec = (sys.argv[1] if len(sys.argv) > 1 else "IMOEX").upper()
    eng, mkt, brd = discover_index_board(sec)
    sys.stderr.write(f"[moex_index_fetch] {sec} board: {eng}/{mkt}/{brd}\n")
    hist = fetch_index_history(sec, eng, mkt, brd)
    print(f"rows={len(hist)}  first={hist[0] if hist else None}  last={hist[-1] if hist else None}")
    print("snapshot:", fetch_index_snapshot(sec, eng, mkt, brd))
