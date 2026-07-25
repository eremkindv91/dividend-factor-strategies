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
from collections import Counter
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import dividend_calendar_core as core


SDK_PACKAGE = "t-tech-investments==1.49.2"
SDK_TARGET = "invest-public-api.tbank.ru"
DEFAULT_MAX_CONSECUTIVE_FAILURES = 5


class TInvestRequestError(RuntimeError):
    """Sanitized API failure safe to aggregate in public source health."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


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
    money = row.get("dividend_net") or row.get("dividendNet")
    currency = money.get("currency") if isinstance(money, dict) else row.get("currency")
    return {
        "record_date": timestamp_to_date(row.get("record_date") or row.get("recordDate")),
        "last_buy_date": timestamp_to_date(row.get("last_buy_date") or row.get("lastBuyDate")),
        "payment_date": timestamp_to_date(row.get("payment_date") or row.get("paymentDate")),
        "declared_date": timestamp_to_date(row.get("declared_date") or row.get("declaredDate")),
        "dividend_value": quotation_to_float(money),
        "currency": core.normalize_currency(currency),
        "cancelled": cancelled,
    }


def build_discovery_events(
    universe: dict[str, dict],
    payloads: dict[str, list[dict]],
    prices: dict[str, dict],
    observed_at: str,
    today: date,
    horizon: date,
    existing_events: list[dict],
) -> list[dict]:
    """Create non-actionable future events found only in the broker calendar."""
    existing = {
        (str(event.get("isin") or event.get("secid") or "").upper(), str(event.get("record_date") or ""))
        for event in existing_events
    }
    discovered = []
    for ticker, security in sorted(universe.items()):
        identifier = str(security.get("isin") or security.get("secid") or ticker).strip()
        rows = payloads.get(identifier)
        if rows is None and security.get("secid"):
            rows = payloads.get(str(security["secid"]))
        for raw in rows or []:
            item = normalize_dividend(raw) if isinstance(raw, dict) else {}
            record = core.parse_date(item.get("record_date"))
            amount = item.get("dividend_value")
            if record is None or not today <= record <= horizon or item.get("cancelled"):
                continue
            security_key = str(security.get("isin") or security.get("secid") or ticker).upper()
            if (security_key, record.isoformat()) in existing:
                continue
            if not core.is_finite_number(amount) or amount <= 0:
                continue
            explicit_last_buy = core.parse_date(item.get("last_buy_date"))
            last_buy, last_buy_source = core.calculate_last_buy_date(
                record, explicit_last_buy_date=explicit_last_buy)
            price_row = prices.get(ticker) or {}
            price = price_row.get("price") if core.is_finite_number(price_row.get("price")) and price_row["price"] > 0 else None
            currency = item.get("currency")
            price_currency = "RUB" if price is not None else None
            price_status = "fresh" if price is not None and price_row.get("fresh") else ("stale" if price is not None else "missing")
            price_field = price_row.get("price_field") if price_status == "fresh" else ("cache" if price is not None else None)
            yield_pct = round(float(amount) / float(price) * 100, 6) if price is not None and currency == price_currency else None
            flags = ["broker_discovery_only", "not_confirmed_by_moex"]
            if price_status == "stale":
                flags.append("stale_price")
            if price_status == "missing":
                flags.append("missing_price")
            if price is not None and currency != price_currency:
                flags.append("currency_mismatch")
            fields = ["record_date", "dividend_value"]
            fields += [field for field in ("last_buy_date", "payment_date", "declared_date", "currency") if item.get(field)]
            event = {
                "id": core.stable_event_id(security_key, record.isoformat()),
                "secid": str(security.get("secid") or ticker).upper(), "isin": security.get("isin"),
                "boardid": security.get("board") or "TQBR", "name": security.get("name") or ticker,
                "share_class": security.get("share_class") or "unknown", "period": None,
                "event_kind": "cash_dividend", "decision_status": "unknown",
                "verification_status": "broker_structured_discovery", "dividend_value": round(float(amount), 8),
                "currency": currency, "decision_date": None, "declared_date": item.get("declared_date"),
                "record_date": record.isoformat(), "last_buy_date": last_buy.isoformat(),
                "last_buy_date_source": "tinvest_explicit" if explicit_last_buy else last_buy_source,
                "ex_dividend_date": core.next_trading_day_from(last_buy).isoformat(),
                "payment_date": item.get("payment_date"), "payment_date_source": "tinvest" if item.get("payment_date") else None,
                "payment_deadline_nominee": core.add_working_days(record, 10).isoformat(),
                "payment_deadline_registered": core.add_working_days(record, 25).isoformat(),
                "price": price, "price_currency": price_currency, "price_asof": price_row.get("asof") if price is not None else None,
                "price_field": price_field, "price_status": price_status, "yield_pct": yield_pct,
                "event_status": None, "days_to_last_buy": max(0, (last_buy - today).days) if last_buy >= today else None,
                "trading_sessions_to_last_buy": core.trading_sessions_until(today, last_buy),
                "source_evidence": [{"source": "tinvest", "source_url": "https://www.tbank.ru/invest/",
                                     "observed_at": observed_at, "fields": fields}],
                "field_provenance": {field: "tinvest" for field in fields},
                "quality_flags": sorted(flags), "has_conflict": False, "conflicts": [],
                "change_type": "new", "changed_fields": [], "first_seen_at": observed_at,
                "last_changed_at": observed_at, "last_verified_at": observed_at,
            }
            event["event_status"] = core.derive_event_status(event, today)
            discovered.append(event)
            existing.add((security_key, record.isoformat()))
    return discovered


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


def _sdk_error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    name = str(getattr(code, "name", code) or "").lower()
    return {
        "unauthenticated": "http_401",
        "permission_denied": "http_403",
        "deadline_exceeded": "timeout",
        "resource_exhausted": "http_429",
        "unavailable": "network_error",
    }.get(name, f"grpc_{name}" if name and name.replace("_", "").isalnum() else "sdk_error")


def _sdk_response_payload(method: str, response) -> dict:
    if method == "FindInstrument":
        return {"instruments": [
            {"isin": str(getattr(item, "isin", "") or ""),
             "ticker": str(getattr(item, "ticker", "") or ""),
             "uid": str(getattr(item, "uid", "") or ""),
             "figi": str(getattr(item, "figi", "") or "")}
            for item in (getattr(response, "instruments", None) or [])
        ]}
    if method == "GetDividends":
        rows = []
        for item in (getattr(response, "dividends", None) or []):
            money = getattr(item, "dividend_net", None)
            rows.append({
                "recordDate": getattr(item, "record_date", None).isoformat()
                if getattr(item, "record_date", None) else None,
                "lastBuyDate": getattr(item, "last_buy_date", None).isoformat()
                if getattr(item, "last_buy_date", None) else None,
                "paymentDate": getattr(item, "payment_date", None).isoformat()
                if getattr(item, "payment_date", None) else None,
                "declaredDate": getattr(item, "declared_date", None).isoformat()
                if getattr(item, "declared_date", None) else None,
                "dividendNet": {"units": getattr(money, "units", 0), "nano": getattr(money, "nano", 0),
                                "currency": str(getattr(money, "currency", "") or "")}
                if money is not None else None,
                "dividendType": str(getattr(item, "dividend_type", "") or ""),
            })
        return {"dividends": rows}
    raise TInvestRequestError("unsupported_method")


@contextmanager
def _sdk_requester(token: str):
    os.environ.setdefault("SSL_TBANK_VERIFY", "True")
    try:
        from t_tech.invest import Client, RequestError
    except ModuleNotFoundError as exc:
        raise TInvestRequestError("sdk_missing") from exc

    with Client(token, target=SDK_TARGET, app_name="dividend-factor-strategies") as client:
        def request(_token: str, method: str, payload: dict) -> dict:
            try:
                if method == "FindInstrument":
                    response = client.instruments.find_instrument(query=str(payload.get("query") or ""))
                elif method == "GetDividends":
                    response = client.instruments.get_dividends(
                        instrument_id=str(payload.get("instrumentId") or ""),
                        from_=datetime.fromisoformat(str(payload["from"]).replace("Z", "+00:00")),
                        to=datetime.fromisoformat(str(payload["to"]).replace("Z", "+00:00")),
                    )
                else:
                    raise TInvestRequestError("unsupported_method")
            except RequestError as exc:
                raise TInvestRequestError(_sdk_error_code(exc)) from exc
            return _sdk_response_payload(method, response)

        yield request


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, TInvestRequestError):
        return exc.code
    name = type(exc).__name__.strip().lower()
    return f"unexpected_{name}" if name and name.replace("_", "").isalnum() else "unexpected_error"


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
    max_instruments: int = 400,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    cache: dict | None = None,
) -> tuple[dict[str, list[dict]], dict]:
    """Fetch at most one structured response per unique instrument identifier.

    ``max_instruments`` keeps the optional broker enrichment polite; it does not
    affect MOEX collection or publication correctness. Он должен покрывать ВСЮ вселенную:
    T-Invest — единственный источник АНОНСИРОВАННЫХ дивидендов (MOEX ISS отдаёт только
    исторический реестр, отстающий больше чем на год), поэтому при отсечке инструменты за
    границей окна вообще не опрашиваются и их анонсы не попадают в календарь (так пропал
    Русагро: limited=228 из 270). Дефолт 400 покрывает текущую вселенную с запасом;
    от перегрузки защищает circuit breaker (max_consecutive_failures).
    """
    if not token:
        return {}, {"status": "disabled", "enriched": 0, "success": 0, "cache_used": 0,
                    "failed": 0, "limited": 0, "errors": {}}
    cache = cache if cache is not None else {"schema_version": 1, "records": {}}
    cached_records = cache.setdefault("records", {})
    identifiers: list[str] = []
    for event in events:
        value = str(event.get("isin") or event.get("secid") or "").strip()
        if value and value not in identifiers:
            identifiers.append(value)
    payloads: dict[str, list[dict]] = {}
    success = cache_used = failed = 0
    errors: Counter[str] = Counter()
    consecutive_failures = 0
    selected_identifiers = identifiers[:max_instruments]
    try:
        request_context = nullcontext(post) if post is not None else _sdk_requester(token)
        with request_context as request:
            for index, identifier in enumerate(selected_identifiers):
                stage = "find_instrument"
                try:
                    found = request(token, "FindInstrument", {"query": identifier})
                    instrument_id = _instrument_id(found, identifier)
                    if not instrument_id:
                        failed += 1
                        errors["instrument_not_found"] += 1
                        continue
                    stage = "get_dividends"
                    body = request(token, "GetDividends", {"instrumentId": instrument_id, "from": f"{from_date}T00:00:00Z", "to": f"{to_date}T23:59:59Z"})
                    rows = body.get("dividends") or []
                    payloads[identifier] = rows if isinstance(rows, list) else []
                    cached_records[identifier] = {"rows": payloads[identifier], "instrument_id": instrument_id}
                    success += 1
                    consecutive_failures = 0
                except Exception as exc:  # Aggregate only a sanitized code; never expose token or response body.
                    error_code = _safe_error_code(exc)
                    cached = cached_records.get(identifier)
                    if isinstance(cached, dict) and isinstance(cached.get("rows"), list):
                        payloads[identifier] = cached["rows"]
                        cache_used += 1
                    else:
                        failed += 1
                        errors[f"{stage}_{error_code}"] += 1
                    consecutive_failures += 1
                    auth_failure = error_code in {"http_401", "http_403"}
                    if auth_failure or consecutive_failures >= max(1, max_consecutive_failures):
                        for remaining in selected_identifiers[index + 1:]:
                            cached = cached_records.get(remaining)
                            if isinstance(cached, dict) and isinstance(cached.get("rows"), list):
                                payloads[remaining] = cached["rows"]
                                cache_used += 1
                            else:
                                failed += 1
                                errors["skipped_after_circuit_breaker"] += 1
                        break
    except Exception as exc:
        error_code = _safe_error_code(exc)
        for identifier in selected_identifiers:
            cached = cached_records.get(identifier)
            if isinstance(cached, dict) and isinstance(cached.get("rows"), list):
                payloads[identifier] = cached["rows"]
                cache_used += 1
            else:
                failed += 1
        errors[f"sdk_start_{error_code}"] += 1
    return payloads, {
        "status": "fresh" if success and not cache_used and not failed else ("partial" if success else ("fallback" if cache_used else "unavailable")),
        "enriched": 0,
        "success": success,
        "cache_used": cache_used,
        "failed": failed,
        "limited": max(0, len(identifiers) - max_instruments),
        "errors": dict(sorted(errors.items())),
    }
