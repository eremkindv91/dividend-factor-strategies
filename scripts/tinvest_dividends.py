#!/usr/bin/env python3
"""Optional T-Invest dividend enrichment for the official MOEX calendar.

The adapter never creates a dividend event on its own: MOEX remains the baseline.
T-Invest may add explicit payment/last-buy dates and an explicit cancellation.  This
keeps a missing token or a broker API incident from changing the public calendar.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable


API_BASE = "https://invest-public-api.tinkoff.ru/rest/tinkoff.public.invest.api.contract.v1.InstrumentsService"
DEFAULT_TIMEOUT_SECONDS = 20


def quotation_to_float(value) -> float | None:
    if not isinstance(value, dict):
        return None
    try:
        return float(value.get("units", 0)) + float(value.get("nano", 0)) / 1_000_000_000
    except (TypeError, ValueError):
        return None


def timestamp_to_date(value) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.replace("Z", "+00:00")[:10]).isoformat()
        except ValueError:
            return None
    if isinstance(value, dict):
        try:
            seconds = int(value.get("seconds", 0))
        except (TypeError, ValueError):
            return None
        if seconds:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
    return None


def normalize_dividend(row: dict) -> dict:
    """Normalize one official API record without assigning an investment status."""
    kind = str(row.get("dividend_type") or row.get("dividendType") or "").lower()
    cancelled = bool(row.get("cancelled")) or "cancel" in kind
    return {
        "record_date": timestamp_to_date(row.get("record_date") or row.get("recordDate")),
        "last_buy_date": timestamp_to_date(row.get("last_buy_date") or row.get("lastBuyDate")),
        "payment_date": timestamp_to_date(row.get("payment_date") or row.get("paymentDate")),
        "declared_date": timestamp_to_date(row.get("declared_date") or row.get("declaredDate")),
        "dividend_value": quotation_to_float(row.get("dividend_net") or row.get("dividendNet")),
        "cancelled": cancelled,
    }


def select_matching_dividend(event: dict, rows: list[dict]) -> dict | None:
    record_date = str(event.get("record_date") or "")
    normalized = [normalize_dividend(row) for row in rows if isinstance(row, dict)]
    exact = [row for row in normalized if row.get("record_date") == record_date]
    if len(exact) == 1:
        return exact[0]
    return None


def apply_tinvest_enrichment(events: list[dict], payloads: dict[str, list[dict]], observed_at: str) -> tuple[list[dict], int]:
    """Apply only exact-record-date T-Invest fields to existing MOEX events."""
    enriched = 0
    for event in events:
        keys = [str(event.get("isin") or ""), str(event.get("secid") or "")]
        rows = next((payloads.get(key) for key in keys if key and payloads.get(key) is not None), None)
        match = select_matching_dividend(event, rows or [])
        if not match:
            continue
        evidence = event.setdefault("source_evidence", [])
        evidence.append({
            "source": "tinvest",
            "source_url": "https://www.tbank.ru/invest/",
            "observed_at": observed_at,
            "fields": [name for name in ("last_buy_date", "payment_date", "declared_date") if match.get(name)] + (["cancelled"] if match["cancelled"] else []),
        })
        provenance = event.setdefault("field_provenance", {})
        if match.get("last_buy_date"):
            event["last_buy_date"] = match["last_buy_date"]
            event["last_buy_date_source"] = "tinvest_explicit"
            provenance["last_buy_date"] = "tinvest"
        if match.get("payment_date"):
            event["payment_date"] = match["payment_date"]
            event["payment_date_source"] = "tinvest"
            provenance["payment_date"] = "tinvest"
        if match.get("declared_date"):
            event["declared_date"] = match["declared_date"]
            provenance["declared_date"] = "tinvest"
        if match["cancelled"]:
            event["decision_status"] = "cancelled"
            event["verification_status"] = "broker_structured_cancellation"
            provenance["decision_status"] = "tinvest_cancelled"
        enriched += 1
    return events, enriched


def _post(token: str, method: str, payload: dict, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    import requests

    response = requests.post(
        f"{API_BASE}/{method}", json=payload, timeout=timeout,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"T-Invest HTTP {response.status_code}")
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError("T-Invest returned a non-object payload")
    return body


def _instrument_id(body: dict, identifier: str) -> str | None:
    candidates = body.get("instruments") or []
    normalized = str(identifier).upper()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        if str(item.get("isin") or "").upper() == normalized or str(item.get("ticker") or "").upper() == normalized:
            return str(item.get("uid") or item.get("figi") or "") or None
    if len(candidates) == 1 and isinstance(candidates[0], dict):
        return str(candidates[0].get("uid") or candidates[0].get("figi") or "") or None
    return None


def load_cache(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("records"), dict):
            return value
    except (OSError, ValueError):
        pass
    return {"schema_version": 1, "records": {}}


def save_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tinvest-dividends-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def collect_payloads(
    events: list[dict],
    token: str,
    from_date: str,
    to_date: str,
    post: Callable[[str, str, dict], dict] | None = None,
    max_instruments: int = 120,
    cache: dict | None = None,
) -> tuple[dict[str, list[dict]], dict]:
    """Fetch at most one structured response per unique instrument identifier.

    ``max_instruments`` keeps the optional broker enrichment polite; it does not
    affect MOEX collection or publication correctness.
    """
    if not token:
        return {}, {"status": "disabled", "enriched": 0, "success": 0, "cache_used": 0, "failed": 0, "limited": 0}
    request = post or _post
    cache = cache if cache is not None else {"schema_version": 1, "records": {}}
    cached_records = cache.setdefault("records", {})
    identifiers: list[str] = []
    for event in events:
        value = str(event.get("isin") or event.get("secid") or "").strip()
        if value and value not in identifiers:
            identifiers.append(value)
    payloads: dict[str, list[dict]] = {}
    success = cache_used = failed = 0
    for identifier in identifiers[:max_instruments]:
        try:
            found = request(token, "FindInstrument", {"query": identifier})
            instrument_id = _instrument_id(found, identifier)
            if not instrument_id:
                failed += 1
                continue
            body = request(token, "GetDividends", {"instrumentId": instrument_id, "from": f"{from_date}T00:00:00Z", "to": f"{to_date}T23:59:59Z"})
            rows = body.get("dividends") or []
            payloads[identifier] = rows if isinstance(rows, list) else []
            cached_records[identifier] = {"rows": payloads[identifier], "instrument_id": instrument_id}
            success += 1
        except Exception:  # The caller reports aggregate health; never expose a token or response body.
            cached = cached_records.get(identifier)
            if isinstance(cached, dict) and isinstance(cached.get("rows"), list):
                payloads[identifier] = cached["rows"]
                cache_used += 1
            else:
                failed += 1
    return payloads, {
        "status": "fresh" if success and not cache_used and not failed else ("partial" if success else ("fallback" if cache_used else "unavailable")),
        "enriched": 0,
        "success": success,
        "cache_used": cache_used,
        "failed": failed,
        "limited": max(0, len(identifiers) - max_instruments),
    }
