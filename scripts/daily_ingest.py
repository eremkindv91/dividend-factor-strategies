#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily Market Data Foundation — Фаза 2: инкрементальный ingestion дневного OHLCV.

Для каждой active-бумаги security master тянет дневные свечи MOEX ISS и хранит в
data/daily/prices/{SECID}.parquet + data/daily/manifest.json. Инкрементально (дотягивает
только новые даты с перекрытием), идемпотентно (checksum по данным), атомарно, с last-good
(источник упал → сохраняем прежний ряд, помечаем fallback). Fetch инъектируется → тесты
детерминированы, без сети.

Правила (quant-honesty): выходной/праздник НЕ создаёт наблюдение; будущие даты отбраковываются;
пропуск сессии остаётся дырой (NaN на downstream-выравнивании, не forward-fill).

CLI: python scripts/daily_ingest.py [--limit N] [--tickers SBER,GAZP] [--years 8]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_store as store  # noqa: E402
import trading_calendar as tc  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = os.path.join(REPO, "data", "security_master.json")
OUT_DIR = os.path.join(REPO, "data", "daily")
PRICES_DIR = os.path.join(OUT_DIR, "prices")
MANIFEST = os.path.join(OUT_DIR, "manifest.json")

OVERLAP_DAYS = 7          # перекрытие при инкременте — ловит ревизии последних свечей
DEFAULT_YEARS = 8
SOURCE = "MOEX ISS"


def _active_secids(master_path: str) -> list[tuple[str, str]]:
    """[(secid, board)] активных бумаг из security master."""
    try:
        m = json.load(open(master_path, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [(s["secid"], s.get("board", "TQBR")) for s in m.get("securities", [])
            if s.get("status") == "active" and s.get("secid")]


def merge_series(existing: list[dict], fetched: list[dict], today: date) -> list[dict]:
    """Инкрементальное слияние: новые даты поверх старых; будущие даты отбрасываются."""
    combined = list(existing) + [r for r in fetched if str(r.get("trade_date"))[:10] <= today.isoformat()]
    return store.canonical_rows(combined)     # дедуп keep-last + сортировка


def ingest_one(secid: str, board: str, existing: list[dict], fetch, today: date,
               years: int = DEFAULT_YEARS) -> tuple[list[dict], str]:
    """Вернуть (ряд, status). status: ok | ok_incremental | fallback | unavailable."""
    if existing:
        last = max(r["trade_date"] for r in existing)
        frm = (date.fromisoformat(last) - timedelta(days=OVERLAP_DAYS)).isoformat()
        incremental = True
    else:
        frm = (today - timedelta(days=int(years * 366))).isoformat()
        incremental = False
    till = today.isoformat()
    try:
        fetched = fetch(secid, frm, till, board)
    except Exception as e:  # noqa: BLE001 — источник упал
        sys.stderr.write(f"[daily] {secid}: fetch fail ({str(e)[:80]})\n")
        return (existing, "fallback" if existing else "unavailable")
    if not fetched:
        return (existing, "fallback" if existing else "unavailable")
    merged = merge_series(existing, fetched, today)
    return (merged, "ok_incremental" if incremental else "ok")


def run(master_path: str = MASTER, out_dir: str = OUT_DIR, fetch=None, today: date | None = None,
        limit: int | None = None, only: list[str] | None = None, years: int = DEFAULT_YEARS) -> dict:
    import moex_iss
    fetch = fetch or moex_iss.fetch_ohlcv
    today = today or tc.to_msk(datetime.now(timezone.utc)).date()
    prices_dir = os.path.join(out_dir, "prices")
    manifest_path = os.path.join(out_dir, "manifest.json")
    manifest = store.load_manifest(manifest_path)
    manifest["schema_version"] = store.SCHEMA_VERSION
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    secids = _active_secids(master_path)
    if only:
        secids = [(s, b) for s, b in secids if s in set(only)]
    if limit:
        secids = secids[:limit]

    for secid, board in secids:
        path = os.path.join(prices_dir, f"{secid}.parquet")
        existing = store.read_parquet(path)
        rows, status = ingest_one(secid, board, existing, fetch, today, years)
        if rows:
            store.write_parquet(path, rows)
            manifest["series"][secid] = {
                "rows": len(rows), "first_date": rows[0]["trade_date"], "last_date": rows[-1]["trade_date"],
                "checksum": store.checksum(rows), "source": SOURCE, "source_as_of": rows[-1]["trade_date"],
                "status": status, "fallback_status": "fallback" if status == "fallback" else "none",
            }
        else:
            manifest["series"][secid] = {"rows": 0, "first_date": None, "last_date": None,
                                         "checksum": None, "source": SOURCE, "source_as_of": None,
                                         "status": "unavailable", "fallback_status": "none"}
    store.write_manifest(manifest_path, manifest)
    ok = sum(1 for v in manifest["series"].values() if v["status"] in ("ok", "ok_incremental"))
    sys.stderr.write(f"[daily] ingested {ok}/{len(secids)} secids → {out_dir}\n")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tickers", type=str, default=None)
    ap.add_argument("--years", type=int, default=DEFAULT_YEARS)
    a = ap.parse_args()
    only = [t.strip().upper() for t in a.tickers.split(",")] if a.tickers else None
    run(limit=a.limit, only=only, years=a.years)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
