#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the backward-compatible short market events calendar.

Official dividend events are projected from ``dividend_calendar.json`` so the
short block and the full calendar share one normalization and settlement core.
SmartLab remains a clearly marked discovery-only source for the legacy block.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dividend_calendar_core as dividend_core  # noqa: E402
from events_config import (  # noqa: E402
    BACK_DAYS, CBR_RATE_MEETINGS, CBR_SOURCE_URL, DATA_QUALITY_PENALTY, EVENT_IMPORTANCE,
    EVENT_LABELS, FORWARD_DAYS, MCAP_BONUS_MAX, URGENCY_BONUS_TODAY,
)
import trading_calendar as tc  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(REPO, "site")
FULL_CALENDAR = os.path.join(SITE, "dividend_calendar.json")


def clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def prev_business_day(value: date) -> date:
    """Backward-compatible helper, now based on settlement-aware trading dates."""
    return dividend_core.calculate_last_buy_date(value, settlement_cycle=1)[0]


def load_universe():
    try:
        with open(os.path.join(SITE, "data.json"), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    universe = {}
    for row in data.get("tickers") or []:
        ticker = row.get("ticker")
        if ticker:
            universe[ticker] = {"name": row.get("name") or ticker, "mcap": row.get("mcap")}
    if not universe:
        try:
            with open(os.path.join(REPO, "data", "security_master.json"), encoding="utf-8") as handle:
                master = json.load(handle)
            for row in master.get("securities") or []:
                ticker = row.get("canonical_ticker") or row.get("secid")
                if ticker and row.get("status") != "delisted":
                    universe[ticker] = {"name": row.get("name") or ticker, "mcap": None}
        except (OSError, ValueError):
            pass
    return universe


def mcap_percentiles(universe):
    caps = sorted(row["mcap"] for row in universe.values()
                  if isinstance(row.get("mcap"), (int, float)) and row["mcap"] > 0)
    if not caps:
        return {}
    return {ticker: sum(1 for cap in caps if cap <= row["mcap"]) / len(caps)
            for ticker, row in universe.items()
            if isinstance(row.get("mcap"), (int, float)) and row["mcap"] > 0}


def composite_importance(base, mcap_pct, days_ahead, data_status):
    score = base + MCAP_BONUS_MAX * (mcap_pct or 0)
    if days_ahead == 0:
        score += URGENCY_BONUS_TODAY
    if data_status != "fresh":
        score -= DATA_QUALITY_PENALTY
    return int(round(clamp(score)))


def _ev(eid, event_date, ticker, company, event_type, description, importance, source, url, data_status):
    title = EVENT_LABELS.get(event_type, (event_type, "Событие"))[0]
    return {
        "id": eid, "date": event_date.isoformat(), "time_msk": None, "ticker": ticker,
        "company": company, "event_type": event_type, "title": title, "description": description,
        "importance": int(importance), "portfolio_relevant": False, "source": source,
        "source_url": url, "data_status": data_status,
    }


def build_smartlab_dividend_events(universe, percentiles, today, start, horizon, existing_keys):
    """Discovery-only candidates for the legacy block; never official/actionable."""
    try:
        import fetch_smartlab_dividends as smartlab
    except Exception:  # noqa: BLE001
        return []
    events = []
    for row in smartlab.fetch_dividends(set(universe)):
        ticker = row.get("ticker")
        record = dividend_core.parse_date(row.get("record_date"))
        if ticker not in universe or record is None:
            continue
        info, mcap_pct, amount = universe[ticker], percentiles.get(ticker, 0.0), row.get("dividend")
        yield_text = f" (≈{row['yield']:.1f}% к цене)" if isinstance(row.get("yield"), (int, float)) else ""
        amount_text = f"₽{amount:.2f} на акцию" if isinstance(amount, (int, float)) else "объявленный дивиденд"
        note = "Discovery-календарь SmartLab; это не официальное подтверждение, сверяйте с эмитентом/MOEX."
        if start <= record <= horizon and (ticker, record.isoformat(), "dividend_registry_close") not in existing_keys:
            events.append(_ev(
                f"{ticker}-{record.isoformat()}-registry-sl", record, ticker, info["name"],
                "dividend_registry_close", f"{amount_text}{yield_text}. {note}",
                composite_importance(EVENT_IMPORTANCE["dividend_registry_close"], mcap_pct,
                                     (record - today).days, "announced"),
                "smartlab", "https://smart-lab.ru/dividends/", "announced"))
        explicit = dividend_core.parse_date(row.get("buy_before"))
        last_buy = explicit or dividend_core.calculate_last_buy_date(record)[0]
        if start <= last_buy <= horizon and (ticker, last_buy.isoformat(), "last_buy_day") not in existing_keys:
            events.append(_ev(
                f"{ticker}-{record.isoformat()}-lastbuy-sl", last_buy, ticker, info["name"],
                "last_buy_day", f"Последний день покупки под {amount_text}{yield_text}; реестр {record}. {note}",
                composite_importance(EVENT_IMPORTANCE["last_buy_day"], mcap_pct,
                                     (last_buy - today).days, "announced"),
                "smartlab", "https://smart-lab.ru/dividends/", "announced"))
    return events


def _cbr_event(meeting_date):
    meeting = next((row for row in CBR_RATE_MEETINGS if row.get("date") == meeting_date.isoformat()), {})
    key = " (опорное, со среднесрочным прогнозом)" if meeting.get("is_key") else ""
    return _ev(
        f"CBR-{meeting_date.isoformat()}-rate", meeting_date, None, "Банк России", "cbr_rate_decision",
        f"Заседание Совета директоров Банка России по ключевой ставке{key}. Решение обычно публикуется в 13:30 МСК.",
        EVENT_IMPORTANCE["cbr_rate_decision"], "cbr_schedule", CBR_SOURCE_URL, "scheduled")


def build_cbr_events(start, horizon, today=None):
    today = today or date.today()
    events, included, future = [], set(), []
    for meeting in CBR_RATE_MEETINGS:
        meeting_date = dividend_core.parse_date(meeting.get("date"))
        if meeting_date is None:
            continue
        if start <= meeting_date <= horizon:
            events.append(_cbr_event(meeting_date)); included.add(meeting_date)
        elif meeting_date >= today:
            future.append(meeting_date)
    if future and min(future) not in included:
        events.append(_cbr_event(min(future)))
    return events


def load_full_calendar(path):
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("schema_version") != dividend_core.SCHEMA_VERSION or not isinstance(payload.get("events"), list):
            raise ValueError("unsupported dividend calendar")
        return payload
    except (OSError, ValueError):
        return {"schema_version": dividend_core.SCHEMA_VERSION, "meta": {"status": "broken"}, "events": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(SITE, "events_calendar.json"))
    parser.add_argument("--dividend-calendar", default=FULL_CALENDAR)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    now = datetime.now(tc.MSK).replace(microsecond=0)
    today = now.date()
    start, horizon = today - timedelta(days=BACK_DAYS), today + timedelta(days=FORWARD_DAYS)
    universe = load_universe()
    if args.limit:
        universe = dict(list(sorted(universe.items()))[:args.limit])
    percentiles = mcap_percentiles(universe)
    full = load_full_calendar(args.dividend_calendar)
    official = dividend_core.legacy_events_from_calendar(full, today, start, horizon)
    keys = {(row.get("ticker"), row.get("date"), row.get("event_type")) for row in official}
    discovery = build_smartlab_dividend_events(universe, percentiles, today, start, horizon, keys)
    cbr = build_cbr_events(start, horizon, today)
    events = official + discovery + cbr
    events.sort(key=lambda row: (-row["importance"], row["date"], row["id"]))

    health = ((full.get("meta") or {}).get("source_health") or {}).get("moex") or {}
    sources = sorted({row["source"] for row in events})
    payload = {
        "meta": {
            "generated_at": now.isoformat(), "timezone": "Europe/Moscow",
            "status": (full.get("meta") or {}).get("status") or "broken",
            "source_count": len(sources), "sources": sources, "event_count": len(events),
            "today_event_count": sum(1 for row in events if row["date"] == today.isoformat()),
            "forward_days": FORWARD_DAYS, "moex_live_ok": health.get("success", 0),
            "moex_cache_used": health.get("cache_used", 0),
            "fallback": (full.get("meta") or {}).get("status") == "fallback",
            "disclaimer": "MOEX ISS — официальный рыночный слой; SmartLab — только discovery и не считается подтверждением. Не ИИР.",
        },
        "events": events,
    }
    dividend_core.atomic_write_json(args.out, payload)
    sys.stderr.write(f"[events] events={len(events)} official={len(official)} discovery={len(discovery)} cbr={len(cbr)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
