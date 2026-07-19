#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build ``site/dividend_calendar.json`` from official MOEX ISS data."""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dividend_calendar_core as core  # noqa: E402
import moex_iss  # noqa: E402
import trading_calendar as tc  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "site"
DEFAULT_OUT = SITE / "dividend_calendar.json"
DEFAULT_CACHE = REPO / "model_output" / "dividends_cache.json"
DEFAULT_SNAPSHOT = REPO / "model_output" / "dividend_calendar_previous.json"
DEFAULT_SECURITY_MASTER = REPO / "data" / "security_master.json"
DEFAULT_QUALITY = REPO / "model_output" / "quality_rf.json"
DEFAULT_BACK_DAYS = 400
DEFAULT_FORWARD_DAYS = 370
MAX_WORKERS = 6
FAIL_BREAK = 18
FRESH_COVERAGE = 0.98
PARTIAL_COVERAGE = 0.50


def load_json(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def load_universe(site_data: Path | None = None) -> dict[str, dict]:
    universe: dict[str, dict] = {}
    master = load_json(DEFAULT_SECURITY_MASTER) or {}
    for row in master.get("securities") or []:
        if row.get("status") == "delisted" or row.get("instrument_type") != "share":
            continue
        ticker = str(row.get("canonical_ticker") or row.get("secid") or "").upper()
        if ticker:
            universe[ticker] = {
                "ticker": ticker, "secid": row.get("secid") or ticker, "isin": row.get("isin"),
                "name": row.get("name") or ticker, "share_class": row.get("share_class") or "unknown",
                "board": row.get("board") or "TQBR",
            }
    quality = load_json(DEFAULT_QUALITY) or {}
    for row in quality.get("rows") or []:
        ticker = str(row.get("ticker") or "").upper()
        if ticker and ticker not in universe:
            universe[ticker] = {"ticker": ticker, "secid": ticker, "isin": None,
                                "name": row.get("name") or ticker,
                                "share_class": row.get("security_type") or "unknown", "board": "TQBR"}
    data = load_json(site_data or SITE / "data.json") or {}
    for row in data.get("tickers") or []:
        ticker = str(row.get("ticker") or "").upper()
        if ticker:
            current = universe.setdefault(ticker, {"ticker": ticker, "secid": ticker, "isin": None,
                                                   "share_class": "unknown", "board": "TQBR"})
            current["name"] = row.get("name") or current.get("name") or ticker
            current["site_price"] = row.get("price")
            current["site_price_asof"] = (data.get("meta") or {}).get("price_asof")
    return dict(sorted(universe.items()))


def load_cache(path: Path) -> dict:
    raw = load_json(path) or {}
    if raw.get("schema_version") == core.SCHEMA_VERSION and isinstance(raw.get("records"), dict):
        return raw
    records = {}
    for ticker, rows in raw.items():
        if not isinstance(rows, list):
            continue
        migrated = []
        for row in rows:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                migrated.append({"secid": ticker, "isin": None, "registryclosedate": row[0],
                                 "value": row[1], "currencyid": "RUB"})
            elif isinstance(row, dict):
                migrated.append(row)
        records[ticker] = {"rows": migrated, "observed_at": None}
    return {"schema_version": core.SCHEMA_VERSION, "records": records}


def collect_moex_records(
    universe: dict[str, dict],
    cache: dict,
    observed_at: str,
    fetcher: Callable[[str], list[dict]] = moex_iss.fetch_dividend_records,
    max_workers: int = MAX_WORKERS,
) -> tuple[dict[str, list[dict]], dict]:
    records = cache.setdefault("records", {})
    output: dict[str, list[dict]] = {}
    live_ok = cache_used = failed = consecutive_fail = 0
    tickers = list(universe)

    for offset in range(0, len(tickers), max_workers):
        batch = tickers[offset:offset + max_workers]
        if consecutive_fail >= FAIL_BREAK:
            for ticker in batch + tickers[offset + max_workers:]:
                cached = records.get(ticker)
                if isinstance(cached, dict):
                    output[ticker] = cached.get("rows") or []
                    cache_used += 1
                else:
                    failed += 1
            break
        completed: dict[str, tuple[list[dict] | None, Exception | None]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(fetcher, universe[ticker]["secid"]): ticker for ticker in batch}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    completed[ticker] = (future.result(), None)
                except Exception as exc:  # noqa: BLE001
                    completed[ticker] = (None, exc)
        for ticker in batch:
            rows, error = completed[ticker]
            if error is None and isinstance(rows, list):
                output[ticker] = rows
                records[ticker] = {"rows": rows, "observed_at": observed_at}
                live_ok += 1
                consecutive_fail = 0
            else:
                consecutive_fail += 1
                cached = records.get(ticker)
                if isinstance(cached, dict):
                    output[ticker] = cached.get("rows") or []
                    cache_used += 1
                else:
                    failed += 1
    cache["generated_at"] = observed_at
    return output, {"success": live_ok, "cache_used": cache_used, "failed": failed}


def _price_rows(universe: dict[str, dict]) -> tuple[dict[str, dict], dict]:
    result = moex_iss.get_prices(list(universe))
    prices = result.get("prices") or {}
    for ticker, security in universe.items():
        row = prices.get(ticker) or {}
        if row.get("price") is None and core.is_finite_number(security.get("site_price")):
            prices[ticker] = {"price": security["site_price"], "asof": security.get("site_price_asof"),
                              "price_field": "cache", "fresh": False, "name": security.get("name")}
    return prices, result.get("meta") or {}


def build_payload(
    universe: dict[str, dict],
    source_rows: dict[str, list[dict]],
    source_stats: dict,
    prices: dict[str, dict],
    price_meta: dict,
    previous: dict | None,
    now: datetime,
    back_days: int = DEFAULT_BACK_DAYS,
    forward_days: int = DEFAULT_FORWARD_DAYS,
) -> dict:
    today = now.date()
    start, end = today - timedelta(days=back_days), today + timedelta(days=forward_days)
    has_calendar = Path(getattr(tc, "_OVERRIDES_PATH", "")).exists()
    normalized = []
    for ticker in sorted(universe):
        security = universe[ticker]
        for row in source_rows.get(ticker) or []:
            record = core.parse_date(row.get("registryclosedate"))
            if record is None or not start <= record <= end:
                continue
            event = core.normalize_moex_event(row, security, prices.get(ticker), now.isoformat(timespec="seconds"),
                                              today, calendar_is_fallback=not has_calendar)
            if event:
                normalized.append(event)
    events = core.merge_normalized_events(normalized)
    core.apply_change_tracking(events, previous, now.isoformat(timespec="seconds"))

    covered = source_stats["success"] + source_stats["cache_used"]
    coverage = covered / len(universe) if universe else 0.0
    if source_stats["success"] == 0 and source_stats["cache_used"] > 0:
        status = "fallback"
    elif source_stats["success"] > 0 and coverage >= FRESH_COVERAGE:
        status = "fresh"
    elif coverage >= PARTIAL_COVERAGE:
        status = "partial"
    else:
        status = "broken"
    actionable = [row for row in events if row["decision_status"] in core.ACTIONABLE_DECISIONS
                  and row["decision_status"] != "cancelled" and not row.get("has_conflict")]
    conflicts = sum(1 for row in events if row.get("has_conflict"))
    meta = {
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": "Europe/Moscow",
        "status": status,
        "horizon": {"from": start.isoformat(), "to": end.isoformat()},
        "universe_count": len(universe),
        "universe_coverage_pct": round(coverage * 100, 2),
        "event_count": len(events),
        "actionable_count": len(actionable),
        "recommended_count": sum(1 for row in events if row["decision_status"] == "board_recommended"),
        "cancelled_count": sum(1 for row in events if row["decision_status"] == "cancelled"),
        "conflict_count": conflicts,
        "no_price_count": sum(1 for row in events if row["price_status"] == "missing"),
        "source_health": {
            "moex": {"status": status, **source_stats, "last_successful_live_fetch": now.isoformat(timespec="seconds")
                     if source_stats["success"] else None},
            "prices": {"status": "fresh" if price_meta.get("source_ok") else "fallback",
                       "asof": price_meta.get("price_asof"), "fresh": price_meta.get("n_fresh", 0),
                       "cache_used": price_meta.get("n_cached", 0), "missing": price_meta.get("n_missing", 0)},
            "tinvest": {"status": "disabled", "enriched": 0},
            "disclosure": {"status": "not_configured", "verified": 0},
        },
        "disclaimer": "Официальные рыночные данные MOEX ISS. Статус market_confirmed не означает отдельную проверку решения собрания акционеров. Не ИИР.",
    }
    return {"schema_version": core.SCHEMA_VERSION, "meta": meta, "events": events}


def run_build(args) -> tuple[dict | None, bool]:
    out, cache_path, snapshot_path = Path(args.out), Path(args.cache), Path(args.snapshot)
    previous = load_json(out) or load_json(snapshot_path)
    universe = load_universe(Path(args.site_data) if args.site_data else None)
    if args.limit:
        universe = dict(list(universe.items())[:args.limit])
    now = datetime.now(tc.MSK).replace(microsecond=0)
    cache = load_cache(cache_path)
    source_rows, source_stats = collect_moex_records(universe, cache, now.isoformat())
    core.atomic_write_json(str(cache_path), cache)
    prices, price_meta = _price_rows(universe)
    payload = build_payload(universe, source_rows, source_stats, prices, price_meta, previous, now,
                            args.back_days, args.forward_days)
    if payload["meta"]["status"] == "broken":
        sys.stderr.write("[div-calendar] broken source coverage; current last-good was not replaced\n")
        return None, False
    changed = core.business_signature(payload) != core.business_signature(previous)
    core.atomic_write_json(str(out), payload)
    core.atomic_write_json(str(snapshot_path), payload)
    if args.change_marker:
        Path(args.change_marker).write_text("changed\n" if changed else "unchanged\n", encoding="utf-8")
    meta = payload["meta"]
    sys.stderr.write(
        f"[div-calendar] events={meta['event_count']} actionable={meta['actionable_count']} "
        f"coverage={meta['universe_coverage_pct']}% status={meta['status']} business_changed={changed}\n")
    return payload, changed


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--cache", default=str(DEFAULT_CACHE))
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    parser.add_argument("--site-data", default=None)
    parser.add_argument("--back-days", type=int, default=DEFAULT_BACK_DAYS)
    parser.add_argument("--forward-days", type=int, default=DEFAULT_FORWARD_DAYS)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--change-marker", default=None)
    return parser.parse_args(argv)


def main() -> int:
    payload, _changed = run_build(parse_args())
    return 0 if payload is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
