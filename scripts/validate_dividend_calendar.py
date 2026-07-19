#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict contract validator for ``site/dividend_calendar.json``."""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import date
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dividend_calendar_core as core  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(REPO, "site", "dividend_calendar.json")
MAX_BYTES = 2 * 1024 * 1024
ALLOWED_SOURCE_HOSTS = {
    "iss.moex.com", "moex.com", "www.moex.com", "e-disclosure.ru", "www.e-disclosure.ru",
    "tbank.ru", "www.tbank.ru",
}
DATE_FIELDS = {
    "decision_date", "declared_date", "record_date", "last_buy_date", "ex_dividend_date",
    "payment_date", "payment_deadline_nominee", "payment_deadline_registered", "price_asof",
}


def _parse(value) -> date | None:
    return core.parse_date(value)


def _walk(value, path="root"):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            yield from _walk(child, f"{path}[{idx}]")
    else:
        yield path, value


def _official_approval_evidence(event: dict) -> bool:
    for evidence in event.get("source_evidence") or []:
        if evidence.get("source") in core.OFFICIAL_DECISION_SOURCES and evidence.get("source_url"):
            return True
    return False


def validate_payload(payload: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["верхний уровень должен быть object"]
    if payload.get("schema_version") != core.SCHEMA_VERSION:
        errors.append(f"неподдерживаемая schema_version={payload.get('schema_version')!r}")
    meta = payload.get("meta")
    events = payload.get("events")
    if not isinstance(meta, dict):
        errors.append("нет meta"); meta = {}
    if not isinstance(events, list):
        errors.append("events не массив"); events = []
    if not meta.get("generated_at"):
        errors.append("нет meta.generated_at")
    if meta.get("timezone") != "Europe/Moscow":
        errors.append("meta.timezone должен быть Europe/Moscow")
    if meta.get("status") not in {"fresh", "partial", "fallback"}:
        errors.append(f"непубликуемый meta.status={meta.get('status')!r}")

    identifiers, business_keys = set(), set()
    actionable = cancelled = conflicts = no_price = recommended = 0
    for idx, event in enumerate(events):
        prefix = f"events[{idx}]"
        if not isinstance(event, dict):
            errors.append(f"{prefix}: не object"); continue
        identifier = event.get("id")
        if not identifier or identifier in identifiers:
            errors.append(f"{prefix}: id пустой/дублируется: {identifier!r}")
        identifiers.add(identifier)
        security_key = str(event.get("isin") or event.get("secid") or "").upper()
        business_key = (security_key, event.get("record_date"), event.get("event_kind"))
        if not security_key or business_key in business_keys:
            errors.append(f"{prefix}: пустой/дублирующийся business key {business_key!r}")
        business_keys.add(business_key)
        if identifier and event.get("record_date") and event.get("event_kind"):
            expected = core.stable_event_id(security_key, event["record_date"], event["event_kind"])
            if identifier != expected:
                errors.append(f"{prefix}: id не соответствует stable business key")

        decision, status = event.get("decision_status"), event.get("event_status")
        if decision not in core.DECISION_STATUSES:
            errors.append(f"{prefix}: неизвестный decision_status={decision!r}")
        if status not in core.EVENT_STATUSES:
            errors.append(f"{prefix}: неизвестный event_status={status!r}")
        if decision == "shareholders_approved" and not _official_approval_evidence(event):
            errors.append(f"{prefix}: shareholders_approved без official decision evidence")
        if decision == "shareholders_approved":
            if _parse(event.get("decision_date")) is None:
                errors.append(f"{prefix}: shareholders_approved без decision_date")
            official_fields = {field for evidence in event.get("source_evidence") or []
                               if evidence.get("source") in core.OFFICIAL_DECISION_SOURCES
                               for field in (evidence.get("fields") or [])}
            if not {"decision_status", "record_date", "dividend_value"}.issubset(official_fields):
                errors.append(f"{prefix}: official decision evidence не покрывает status/record/amount")

        parsed = {}
        for field in DATE_FIELDS:
            value = event.get(field)
            if value is not None:
                parsed[field] = _parse(value)
                if parsed[field] is None:
                    errors.append(f"{prefix}: {field} не ISO date: {value!r}")
        record, last_buy = parsed.get("record_date"), parsed.get("last_buy_date")
        if record is None:
            errors.append(f"{prefix}: record_date обязателен")
        if last_buy is None:
            errors.append(f"{prefix}: last_buy_date обязателен")
        if record and last_buy and last_buy > record:
            errors.append(f"{prefix}: last_buy_date позже record_date")
        if record and parsed.get("payment_date") and parsed["payment_date"] < record:
            errors.append(f"{prefix}: payment_date раньше record_date")
        for field in ("payment_deadline_nominee", "payment_deadline_registered"):
            if record and parsed.get(field) and parsed[field] < record:
                errors.append(f"{prefix}: {field} раньше record_date")

        amount = event.get("dividend_value")
        if decision != "cancelled" and (not core.is_finite_number(amount) or amount <= 0):
            errors.append(f"{prefix}: dividend_value должен быть > 0")
        price, yield_pct = event.get("price"), event.get("yield_pct")
        if event.get("price_status") not in {"fresh", "stale", "missing"}:
            errors.append(f"{prefix}: неизвестный price_status={event.get('price_status')!r}")
        if event.get("price_field") not in {"LCLOSEPRICE", "LAST", "PREVPRICE", "cache", None}:
            errors.append(f"{prefix}: неизвестный price_field={event.get('price_field')!r}")
        if event.get("last_buy_date_source") not in {"explicit_source", "calculated_settlement", "tinvest_explicit"}:
            errors.append(f"{prefix}: неизвестный last_buy_date_source")
        if core.is_finite_number(price) and price > 0 and event.get("currency") == event.get("price_currency"):
            expected_yield = amount / price * 100 if core.is_finite_number(amount) else None
            if not core.is_finite_number(yield_pct) or abs(yield_pct - expected_yield) > 0.001:
                errors.append(f"{prefix}: yield_pct не согласована с dividend_value/price")
        elif yield_pct is not None:
            errors.append(f"{prefix}: yield_pct должна быть null без совместимых amount/price")
        if decision in core.ACTIONABLE_DECISIONS and decision != "cancelled":
            if not event.get("source_evidence"):
                errors.append(f"{prefix}: actionable event без evidence")
            if not event.get("has_conflict"):
                actionable += 1
        if decision == "cancelled":
            cancelled += 1
            if status != "cancelled":
                errors.append(f"{prefix}: cancelled decision с event_status={status!r}")
        if decision == "board_recommended":
            recommended += 1
        if event.get("has_conflict"):
            conflicts += 1
            if not event.get("conflicts"):
                errors.append(f"{prefix}: has_conflict=true без conflicts")
        if event.get("price_status") == "missing":
            no_price += 1
        if "in_portfolio" in event:
            errors.append(f"{prefix}: персональное поле in_portfolio запрещено")
        for evidence_idx, evidence in enumerate(event.get("source_evidence") or []):
            url = str(evidence.get("source_url") or "")
            parsed_url = urlparse(url)
            if parsed_url.scheme != "https" or (parsed_url.hostname or "").lower() not in ALLOWED_SOURCE_HOSTS:
                errors.append(f"{prefix}.source_evidence[{evidence_idx}]: URL не в allowlist")

    expected_counts = {
        "event_count": len(events), "actionable_count": actionable, "recommended_count": recommended,
        "cancelled_count": cancelled, "conflict_count": conflicts, "no_price_count": no_price,
    }
    for field, expected in expected_counts.items():
        if meta.get(field) != expected:
            errors.append(f"meta.{field}={meta.get(field)!r}, ожидалось {expected}")
    for path, value in _walk(payload):
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(f"{path}: NaN/Infinity запрещены")
        if isinstance(value, str) and len(value) > 2000:
            errors.append(f"{path}: строка длиннее 2000 символов")
        if path.endswith(".in_portfolio"):
            errors.append(f"{path}: персональное поле запрещено")
    return errors


def main(argv=None) -> int:
    path = (argv or sys.argv[1:])[0] if (argv or sys.argv[1:]) else DEFAULT
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        sys.stderr.write(f"[validate-div-calendar] файл отсутствует/пуст: {path}\n")
        return 2
    if os.path.getsize(path) > MAX_BYTES:
        sys.stderr.write(f"[validate-div-calendar] файл больше {MAX_BYTES} bytes\n")
        return 2
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"[validate-div-calendar] невалидный JSON: {exc}\n")
        return 2
    errors = validate_payload(payload)
    if errors:
        sys.stderr.write("[validate-div-calendar] FAIL:\n  - " + "\n  - ".join(errors) + "\n")
        return 1
    meta = payload["meta"]
    sys.stderr.write(f"[validate-div-calendar] OK: events={meta['event_count']} "
                     f"actionable={meta['actionable_count']} status={meta['status']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
