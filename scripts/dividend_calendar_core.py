#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared, source-agnostic dividend calendar primitives.

The module intentionally contains no network calls. Fetchers live in the builder and
feed normalized records here, which keeps settlement, lifecycle and validation logic
deterministic and fixture-testable.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Callable, Iterable

import trading_calendar as tc

SCHEMA_VERSION = "2.0"
DECISION_STATUSES = {
    "board_recommended", "shareholders_approved", "market_confirmed", "cancelled", "unknown",
}
EVENT_STATUSES = {
    "buyable", "last_buy_today", "ex_dividend", "record_closed", "payment_scheduled",
    "payment_deadline_open", "payment_deadline_passed_unknown", "cancelled",
}
ACTIONABLE_DECISIONS = {"shareholders_approved", "market_confirmed"}
OFFICIAL_DECISION_SOURCES = {"issuer_disclosure", "e_disclosure", "official_issuer"}
SOURCE_PRIORITY = {
    "smartlab": 10,
    "tinvest": 30,
    "moex_iss": 40,
    "board_disclosure": 70,
    "issuer_disclosure": 90,
    "e_disclosure": 90,
    "official_issuer": 90,
    "explicit_cancellation": 100,
}
TRACKED_FIELDS = (
    "decision_status", "verification_status", "dividend_value", "currency", "decision_date",
    "declared_date", "record_date", "last_buy_date", "payment_date", "price", "price_asof",
    "yield_pct", "event_status", "has_conflict", "quality_flags",
)


def parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def is_finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def stable_event_id(isin_or_secid: str, record_date: str, event_kind: str = "cash_dividend") -> str:
    raw = "|".join((str(isin_or_secid).strip().upper(), record_date, event_kind))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def settlement_date(
    trade_date: date,
    settlement_cycle: int = 1,
    is_trading_day: Callable[[date], bool] = tc.is_trading_day,
) -> date:
    """Return the settlement date after ``settlement_cycle`` eligible sessions."""
    if settlement_cycle < 0:
        raise ValueError("settlement_cycle must be non-negative")
    current = trade_date
    completed = 0
    while completed < settlement_cycle:
        current += timedelta(days=1)
        if is_trading_day(current):
            completed += 1
    return current


def calculate_last_buy_date(
    record_date: date,
    settlement_cycle: int = 1,
    is_trading_day: Callable[[date], bool] = tc.is_trading_day,
    explicit_last_buy_date: date | None = None,
) -> tuple[date, str]:
    if explicit_last_buy_date is not None:
        return explicit_last_buy_date, "explicit_source"
    candidate = record_date
    for _ in range(40):
        if is_trading_day(candidate) and settlement_date(candidate, settlement_cycle, is_trading_day) <= record_date:
            return candidate, "calculated_settlement"
        candidate -= timedelta(days=1)
    raise ValueError(f"cannot calculate last buy date for {record_date}")


def next_trading_day_from(
    value: date, is_trading_day: Callable[[date], bool] = tc.is_trading_day,
) -> date:
    current = value + timedelta(days=1)
    while not is_trading_day(current):
        current += timedelta(days=1)
    return current


def add_working_days(
    value: date,
    count: int,
    is_working_day: Callable[[date], bool] = tc.is_trading_day,
) -> date:
    """Add working days without counting the starting date."""
    if count < 0:
        raise ValueError("count must be non-negative")
    current = value
    completed = 0
    while completed < count:
        current += timedelta(days=1)
        if is_working_day(current):
            completed += 1
    return current


def trading_sessions_until(
    today: date,
    last_buy_date: date,
    is_trading_day: Callable[[date], bool] = tc.is_trading_day,
) -> int | None:
    if last_buy_date < today:
        return None
    return sum(1 for offset in range((last_buy_date - today).days + 1)
               if is_trading_day(today + timedelta(days=offset)))


def derive_event_status(event: dict, today: date) -> str:
    if event.get("decision_status") == "cancelled":
        return "cancelled"
    last_buy = parse_date(event.get("last_buy_date"))
    record = parse_date(event.get("record_date"))
    payment = parse_date(event.get("payment_date"))
    deadline = parse_date(event.get("payment_deadline_registered"))
    if last_buy and today < last_buy:
        return "buyable"
    if last_buy and today == last_buy:
        return "last_buy_today"
    if record and today <= record:
        return "ex_dividend"
    if payment and today <= payment:
        return "payment_scheduled"
    if deadline and today <= deadline:
        return "payment_deadline_open"
    if deadline and today > deadline:
        return "payment_deadline_passed_unknown"
    return "record_closed"


def normalize_currency(value) -> str | None:
    cur = str(value or "").upper()
    if cur in {"RUB", "SUR", "RUR"}:
        return "RUB"
    return cur or None


def normalize_moex_event(
    row: dict,
    security: dict,
    price_row: dict | None,
    observed_at: str,
    today: date,
    settlement_cycle: int = 1,
    calendar_is_fallback: bool = False,
) -> dict | None:
    record = parse_date(row.get("registryclosedate"))
    amount = row.get("value")
    if record is None or not is_finite_number(amount) or float(amount) <= 0:
        return None
    currency = normalize_currency(row.get("currencyid"))
    secid = str(row.get("secid") or security.get("secid") or security.get("ticker") or "").upper()
    if not secid:
        return None
    isin = row.get("isin") or security.get("isin")
    last_buy, last_buy_source = calculate_last_buy_date(record, settlement_cycle)
    ex_date = next_trading_day_from(last_buy)
    flags: list[str] = []
    if calendar_is_fallback:
        flags.append("settlement_calendar_fallback")

    p = price_row or {}
    price = p.get("price") if is_finite_number(p.get("price")) and p.get("price") > 0 else None
    price_currency = "RUB" if price is not None else None
    price_status = "fresh" if p.get("fresh") and price is not None else ("stale" if price is not None else "missing")
    price_field = p.get("price_field") if price_status == "fresh" else ("cache" if price is not None else None)
    if price_status == "stale":
        flags.append("stale_price")
    if price_status == "missing":
        flags.append("missing_price")
    yield_pct = None
    if price is not None and currency == price_currency:
        yield_pct = round(float(amount) / float(price) * 100, 6)
    elif price is not None:
        flags.append("currency_mismatch")

    key = isin or secid
    source_url = f"https://iss.moex.com/iss/securities/{secid}/dividends.json"
    event = {
        "id": stable_event_id(key, record.isoformat()),
        "secid": secid,
        "isin": isin,
        "boardid": security.get("board") or "TQBR",
        "name": security.get("name") or secid,
        "share_class": security.get("share_class") or "unknown",
        "period": row.get("period"),
        "event_kind": "cash_dividend",
        "decision_status": "market_confirmed",
        "verification_status": "official_market_data",
        "dividend_value": round(float(amount), 8),
        "currency": currency,
        "decision_date": None,
        "declared_date": None,
        "record_date": record.isoformat(),
        "last_buy_date": last_buy.isoformat(),
        "last_buy_date_source": last_buy_source,
        "ex_dividend_date": ex_date.isoformat(),
        "payment_date": None,
        "payment_date_source": None,
        "payment_deadline_nominee": add_working_days(record, 10).isoformat(),
        "payment_deadline_registered": add_working_days(record, 25).isoformat(),
        "price": price,
        "price_currency": price_currency,
        "price_asof": p.get("asof") if price is not None else None,
        "price_field": price_field,
        "price_status": price_status,
        "yield_pct": yield_pct,
        "event_status": None,
        "days_to_last_buy": max(0, (last_buy - today).days) if last_buy >= today else None,
        "trading_sessions_to_last_buy": trading_sessions_until(today, last_buy),
        "source_evidence": [{
            "source": "moex_iss",
            "source_url": source_url,
            "observed_at": observed_at,
            "fields": ["dividend_value", "currency", "record_date", "isin", "secid"],
        }],
        "field_provenance": {
            "dividend_value": "moex_iss",
            "currency": "moex_iss",
            "record_date": "moex_iss",
            "last_buy_date": last_buy_source,
            "price": "moex_iss" if price_status == "fresh" else ("moex_cache" if price is not None else "missing"),
        },
        "quality_flags": sorted(set(flags)),
        "has_conflict": False,
        "conflicts": [],
        "change_type": "new",
        "changed_fields": [],
        "first_seen_at": observed_at,
        "last_changed_at": observed_at,
        "last_verified_at": observed_at,
    }
    event["event_status"] = derive_event_status(event, today)
    return event


def _source_rank(event: dict) -> int:
    evidence = event.get("source_evidence") or []
    return max((SOURCE_PRIORITY.get(str(row.get("source")), 0) for row in evidence), default=0)


def _amount_conflict(left, right) -> bool:
    if not (is_finite_number(left) and is_finite_number(right)):
        return False
    tolerance = max(0.01, min(abs(float(left)), abs(float(right))) * 0.001)
    return abs(float(left) - float(right)) > tolerance


def merge_normalized_events(events: Iterable[dict]) -> list[dict]:
    """Merge exact business keys and retain material source disagreements."""
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for event in events:
        key = (str(event.get("isin") or event.get("secid") or "").upper(),
               str(event.get("record_date") or ""), str(event.get("event_kind") or ""))
        grouped.setdefault(key, []).append(deepcopy(event))

    merged: list[dict] = []
    for key in sorted(grouped):
        rows = sorted(grouped[key], key=_source_rank, reverse=True)
        current = rows[0]
        for other in rows[1:]:
            current["source_evidence"] = _dedup_evidence(
                (current.get("source_evidence") or []) + (other.get("source_evidence") or []))
            for field, source in (other.get("field_provenance") or {}).items():
                current["field_provenance"].setdefault(field, source)
            if _amount_conflict(current.get("dividend_value"), other.get("dividend_value")):
                current["conflicts"].append({
                    "field": "dividend_value",
                    "values": [current.get("dividend_value"), other.get("dividend_value")],
                    "reason": "material_source_disagreement",
                })
            if other.get("decision_status") == "cancelled":
                current["decision_status"] = "cancelled"
                current["verification_status"] = other.get("verification_status") or "official_cancellation"
            elif _source_rank(other) > _source_rank(current):
                current["decision_status"] = other.get("decision_status", current.get("decision_status"))
        current["has_conflict"] = bool(current.get("conflicts"))
        if current["decision_status"] == "cancelled":
            current["event_status"] = "cancelled"
        merged.append(current)

    by_security: dict[tuple[str, str], list[dict]] = {}
    for event in merged:
        by_security.setdefault((str(event.get("isin") or event.get("secid")), event.get("event_kind")), []).append(event)
    for rows in by_security.values():
        rows.sort(key=lambda row: row.get("record_date") or "")
        for left, right in zip(rows, rows[1:]):
            ld, rd = parse_date(left.get("record_date")), parse_date(right.get("record_date"))
            left_sources = {row.get("source") for row in left.get("source_evidence") or []}
            right_sources = {row.get("source") for row in right.get("source_evidence") or []}
            if ld and rd and abs((rd - ld).days) == 1 and left_sources != right_sources:
                conflict = {"field": "record_date", "values": [left["record_date"], right["record_date"]],
                            "reason": "one_day_source_disagreement_not_auto_merged"}
                for event in (left, right):
                    event["conflicts"].append(conflict)
                    event["has_conflict"] = True
    return sorted(merged, key=lambda event: (event.get("record_date") or "", event.get("secid") or "", event["id"]))


def _dedup_evidence(rows: Iterable[dict]) -> list[dict]:
    seen, out = set(), []
    for row in rows:
        key = (row.get("source"), row.get("source_url"), tuple(sorted(row.get("fields") or [])))
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def apply_change_tracking(events: list[dict], previous: dict | None, observed_at: str) -> list[dict]:
    old = {row.get("id"): row for row in ((previous or {}).get("events") or []) if row.get("id")}
    for event in events:
        prior = old.get(event["id"])
        if prior is None:
            event["change_type"] = "cancelled" if event.get("decision_status") == "cancelled" else "new"
            event["first_seen_at"] = observed_at
            event["last_changed_at"] = observed_at
            event["changed_fields"] = []
        else:
            changed = [field for field in TRACKED_FIELDS if prior.get(field) != event.get(field)]
            event["first_seen_at"] = prior.get("first_seen_at") or observed_at
            event["changed_fields"] = changed
            if event.get("decision_status") == "cancelled" and prior.get("decision_status") != "cancelled":
                event["change_type"] = "cancelled"
            else:
                event["change_type"] = "updated" if changed else "unchanged"
            event["last_changed_at"] = observed_at if changed else (prior.get("last_changed_at") or observed_at)
        event["last_verified_at"] = observed_at
    return events


def event_business_view(event: dict) -> dict:
    volatile = {"change_type", "changed_fields", "first_seen_at", "last_changed_at", "last_verified_at"}
    result = {key: value for key, value in event.items() if key not in volatile}
    for evidence in result.get("source_evidence") or []:
        evidence.pop("observed_at", None)
    return result


def business_signature(payload: dict | None) -> str:
    if not payload:
        return ""
    body = [event_business_view(deepcopy(row)) for row in (payload.get("events") or [])]
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def atomic_write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".calendar-", suffix=".json", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def legacy_events_from_calendar(payload: dict, today: date, start: date, horizon: date) -> list[dict]:
    """Project the full contract into the existing short event-card contract."""
    out = []
    for row in payload.get("events") or []:
        if row.get("decision_status") not in ACTIONABLE_DECISIONS or row.get("has_conflict"):
            continue
        record = parse_date(row.get("record_date"))
        last_buy = parse_date(row.get("last_buy_date"))
        if record is None or last_buy is None:
            continue
        amount = row.get("dividend_value")
        yld = row.get("yield_pct")
        ytxt = f" (≈{yld:.1f}% к цене на {row.get('price_asof')})" if is_finite_number(yld) else ""
        source_url = next((e.get("source_url") for e in row.get("source_evidence") or [] if e.get("source_url")), None)
        status = "stale" if row.get("price_status") == "stale" else "fresh"
        base = {
            "time_msk": None,
            "ticker": row.get("secid"),
            "company": row.get("name") or row.get("secid"),
            "importance": 85,
            "portfolio_relevant": False,
            "source": "moex_iss",
            "source_url": source_url,
            "data_status": status,
        }
        if start <= record <= horizon:
            out.append({**base, "id": f"{row['id']}-registry", "date": record.isoformat(),
                        "event_type": "dividend_registry_close", "title": "Закрытие реестра под дивиденды",
                        "description": f"Дивиденд ₽{amount:.2f} на акцию{ytxt}. Подтверждено официальными данными MOEX."})
        if start <= last_buy <= horizon:
            out.append({**base, "id": f"{row['id']}-lastbuy", "date": last_buy.isoformat(),
                        "event_type": "last_buy_day", "title": "Последний день покупки под дивиденд",
                        "description": f"Последний день покупки под дивиденд ₽{amount:.2f}{ytxt}; реестр {record.isoformat()} (T+1)."})
    return out
