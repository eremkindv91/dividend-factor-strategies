#!/usr/bin/env python3
"""Controlled incorporation of manually verified dividend disclosure decisions.

Discovery pages are useful leads, but a title alone cannot prove a shareholder
decision.  Only rows explicitly marked ``verified`` and carrying the source URL,
decision date, amount and record date can promote an event to
``shareholders_approved``.
"""
from __future__ import annotations

import csv
from copy import deepcopy
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import dividend_calendar_core as core


DECISION_FILE = "data/dividend_disclosure_decisions.csv"
REQUIRED_COLUMNS = (
    "ticker", "isin", "decision_status", "decision_date", "dividend_value", "currency",
    "record_date", "payment_date", "last_buy_date", "source_url", "document_title",
    "review_status", "reviewed_at", "notes",
)
VERIFIED_STATES = {"verified", "approved"}
SUPPORTED_STATUSES = {"board_recommended", "shareholders_approved", "cancelled"}


def _clean(value) -> str:
    return str(value or "").strip()


def _finite_positive(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if core.is_finite_number(parsed) and parsed > 0 else None


def _trusted_hosts(root: Path) -> set[str]:
    hosts = {"e-disclosure.ru", "www.e-disclosure.ru"}
    for relative in ("data/company_sources.csv", "data/companies_registry.csv"):
        path = root / relative
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                for key in ("source_url", "disclosure_page_url", "issuer_website"):
                    host = urlparse(_clean(row.get(key))).hostname
                    if host:
                        hosts.add(host.lower())
    return hosts


def _valid_source_url(value: str, trusted_hosts: set[str]) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and parsed.hostname.lower() in trusted_hosts


def load_verified_decisions(root: Path, path: Path | None = None) -> tuple[list[dict], list[dict]]:
    source = path or root / DECISION_FILE
    if not source.exists():
        return [], []
    trusted_hosts = _trusted_hosts(root)
    rows: list[dict] = []
    rejected: list[dict] = []
    with source.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            row = {key: _clean(raw.get(key)) for key in REQUIRED_COLUMNS}
            ticker = row["ticker"].upper()
            status = row["decision_status"]
            source_url = row["source_url"]
            record = core.parse_date(row["record_date"])
            decision = core.parse_date(row["decision_date"])
            amount = _finite_positive(row["dividend_value"])
            reason = ""
            if row["review_status"].lower() not in VERIFIED_STATES:
                reason = "not_manually_verified"
            elif not ticker or status not in SUPPORTED_STATUSES:
                reason = "ticker_or_status_invalid"
            elif not _valid_source_url(source_url, trusted_hosts):
                reason = "source_url_not_in_verified_issuer_registry"
            elif record is None:
                reason = "record_date_required_for_stable_event_match"
            elif status != "cancelled" and amount is None:
                reason = "dividend_value_invalid"
            elif status == "shareholders_approved" and decision is None:
                reason = "shareholders_approval_requires_decision_date"
            if reason:
                rejected.append({"ticker": ticker, "source_url": source_url, "reason": reason})
                continue
            rows.append({
                **row,
                "ticker": ticker,
                "record_date": record.isoformat() if record else "",
                "decision_date": decision.isoformat() if decision else "",
                "payment_date": (core.parse_date(row["payment_date"]) or None),
                "last_buy_date": (core.parse_date(row["last_buy_date"]) or None),
                "dividend_value": amount,
                "currency": core.normalize_currency(row["currency"]) or "RUB",
            })
    return rows, rejected


def _disclosure_source_name(url: str) -> str:
    return "e_disclosure" if urlparse(url).hostname in {"e-disclosure.ru", "www.e-disclosure.ru"} else "official_issuer"


def _matches(event: dict, row: dict) -> bool:
    if row.get("isin") and str(event.get("isin") or "").upper() == row["isin"].upper():
        return not row.get("record_date") or event.get("record_date") == row["record_date"]
    return str(event.get("secid") or "").upper() == row["ticker"] and event.get("record_date") == row.get("record_date")


def _build_new_event(row: dict, security: dict, price: dict | None, observed_at: str, today: date) -> dict | None:
    if row["decision_status"] == "cancelled" or not row.get("record_date") or row.get("dividend_value") is None:
        return None
    moex_like = {
        "secid": security.get("secid") or row["ticker"], "isin": row.get("isin") or security.get("isin"),
        "registryclosedate": row["record_date"], "value": row["dividend_value"], "currencyid": row["currency"],
    }
    event = core.normalize_moex_event(moex_like, security, price, observed_at, today)
    if not event:
        return None
    event["source_evidence"] = []
    event["field_provenance"] = {}
    return event


def apply_verified_decisions(
    events: list[dict],
    decisions: list[dict],
    universe: dict[str, dict],
    prices: dict[str, dict],
    observed_at: str,
    today: date,
) -> tuple[list[dict], int]:
    """Merge reviewed official decisions without auto-promoting page candidates."""
    output = [deepcopy(event) for event in events]
    applied = 0
    for row in decisions:
        matches = [event for event in output if _matches(event, row)]
        if not matches:
            created = _build_new_event(row, universe.get(row["ticker"], {"ticker": row["ticker"]}), prices.get(row["ticker"]), observed_at, today)
            if not created:
                continue
            output.append(created)
            matches = [created]
        for event in matches:
            old_status = event.get("decision_status")
            status = row["decision_status"]
            if status == "board_recommended" and old_status in {"market_confirmed", "shareholders_approved"}:
                continue
            event["decision_status"] = status
            event["verification_status"] = "official_disclosure"
            if row.get("decision_date"):
                event["decision_date"] = row["decision_date"]
            if row.get("payment_date"):
                event["payment_date"] = row["payment_date"].isoformat()
                event["payment_date_source"] = "official_disclosure"
            if row.get("last_buy_date"):
                event["last_buy_date"] = row["last_buy_date"].isoformat()
                event["last_buy_date_source"] = "official_disclosure"
                event["ex_dividend_date"] = core.next_trading_day_from(row["last_buy_date"]).isoformat()
            if row.get("dividend_value") is not None and status != "cancelled":
                current_amount = event.get("dividend_value")
                if current_amount is not None and core._amount_conflict(current_amount, row["dividend_value"]):
                    event.setdefault("conflicts", []).append({
                        "field": "dividend_value",
                        "values": [current_amount, row["dividend_value"]],
                        "reason": "manual_verified_disclosure_disagrees_with_moex",
                    })
                    event["has_conflict"] = True
                event["dividend_value"] = row["dividend_value"]
                event["currency"] = row["currency"]
            fields = ["decision_status", "decision_date"]
            if row.get("dividend_value") is not None:
                fields.extend(["dividend_value", "currency", "record_date"])
            if row.get("payment_date"):
                fields.append("payment_date")
            if row.get("last_buy_date"):
                fields.append("last_buy_date")
            event.setdefault("source_evidence", []).append({
                "source": _disclosure_source_name(row["source_url"]), "source_url": row["source_url"],
                "observed_at": observed_at, "fields": fields,
            })
            provenance = event.setdefault("field_provenance", {})
            for field in fields:
                provenance[field] = "official_disclosure"
            event["event_status"] = core.derive_event_status(event, today)
            applied += 1
    return core.merge_normalized_events(output), applied
