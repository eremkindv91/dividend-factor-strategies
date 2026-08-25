#!/usr/bin/env python3
"""Build physical-person futures positioning from official MOEX ISS data.

MOEX ``openpositions/{asset}`` is aggregated by underlying asset, not by an
expiring futures series. That makes the position history continuous through a
rollover. A current contract is still resolved and published for auditability;
it is never used to backfill or rewrite historical observations.

The builder is incremental by default and keeps the previous valid data for an
individual symbol when MOEX temporarily fails. Use ``--full-refresh`` to fetch
the complete available history again.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
from urllib.parse import urlencode
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from scripts.moex_http import MoexHTTP, MoexTransportError
except ImportError:  # direct execution
    from moex_http import MoexHTTP, MoexTransportError

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "futures_positions.json"

ISS = "https://iss.moex.com/iss"
SOURCE_PATH = "statistics/engines/futures/markets/forts/openpositions"
UA = "dividend-factor-strategies/positions (+https://github.com/eremkindv91/dividend-factor-strategies)"
HISTORY_FROM = "2012-01-01"
PAUSE = 0.12
MIN_POINTS = 30
Z_WINDOW = 250
HISTORY_KEEP = 800
MOSCOW = ZoneInfo("Europe/Moscow")
USABLE_STATUSES = {"fresh", "delayed_by_exchange", "stale"}
MAX_PAGES = 200
INDEX_ASSETS = {
    "IMOEX": "Вечный фьючерс на Индекс МосБиржи",
    "MIX": "Фьючерс на Индекс МосБиржи",
    "MXI": "Фьючерс на Индекс МосБиржи (мини)",
}


class IssError(RuntimeError):
    """MOEX did not return a usable response."""


class ValidationError(RuntimeError):
    """A source or merged series violates the public data contract."""


class RemoteEmpty(ValidationError):
    """MOEX answered successfully but published no physical-person rows."""


class PaginationIncomplete(ValidationError):
    """MOEX pagination did not reach a provable terminal page."""


class PositionRows(list):
    """List-compatible result carrying source completeness diagnostics."""

    def __init__(self, rows=(), *, diagnostics: dict | None = None):
        super().__init__(rows)
        self.diagnostics = diagnostics or {}


def log(message: str) -> None:
    print(f"[POSITIONING] {message}")


def utc_now(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


_HTTP = MoexHTTP(user_agent=UA, logger=log)


def http_json(url: str, tries: int = 4, timeout: int = 45) -> dict:
    """Compatibility wrapper around the shared persistent MOEX transport."""
    client = _HTTP
    if tries != client.attempts or timeout != client.timeout[1]:
        client = MoexHTTP(
            user_agent=UA, attempts=tries, read_timeout=timeout, logger=log,
        )
    try:
        return client.get_json(url)
    except MoexTransportError as exc:
        raise IssError(str(exc)) from exc


def rows_of(payload: dict, block: str) -> list[dict]:
    item = payload.get(block) or {}
    columns = item.get("columns") or []
    if "ERROR_MESSAGE" in columns:
        data = item.get("data") or []
        message = data[0][columns.index("ERROR_MESSAGE")] if data else "source refusal"
        raise IssError(str(message))
    return [dict(zip(columns, row)) for row in (item.get("data") or [])]


def parse_iso_day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _cursor(payload: dict) -> dict | None:
    rows = rows_of(payload, "open_positions.cursor")
    if not rows:
        return None
    return {str(key).lower(): value for key, value in rows[0].items()}


def positions(asset: str, date_to: str, date_from: str = HISTORY_FROM) -> PositionRows:
    """Return complete physical-person history with explicit pagination diagnostics."""
    base = f"{ISS}/{SOURCE_PATH}/{asset}.json"
    by_date: dict[str, dict] = {}
    raw_rows = 0
    physical_rows = 0
    pages = 0
    start = 0
    seen_page_signatures: set[tuple] = set()
    first_source_date = None
    remote_max_date = None

    while pages < MAX_PAGES:
        params = {"iss.meta": "off", "from": date_from, "till": date_to, "start": start}
        payload = http_json(f"{base}?{urlencode(params)}")
        block = payload.get("open_positions")
        if not isinstance(block, dict):
            raise PaginationIncomplete("open_positions block missing")
        columns = block.get("columns") or []
        required = {"tradedate", "is_fiz", "open_position_long", "open_position_short"}
        if not required.issubset(set(columns)):
            raise PaginationIncomplete(
                "open_positions columns incomplete: " + ",".join(sorted(required - set(columns)))
            )
        page_rows = rows_of(payload, "open_positions")
        pages += 1
        raw_rows += len(page_rows)
        signature = tuple(
            (str(row.get("tradedate") or "")[:10], row.get("is_fiz"),
             row.get("open_position_long"), row.get("open_position_short"))
            for row in page_rows
        )
        if signature and signature in seen_page_signatures:
            raise PaginationIncomplete(f"pagination made no progress at start={start}")
        seen_page_signatures.add(signature)

        source_dates = [str(row.get("tradedate"))[:10] for row in page_rows
                        if parse_iso_day(row.get("tradedate"))]
        if source_dates:
            first_source_date = min(filter(None, [first_source_date, min(source_dates)]))
            remote_max_date = max(filter(None, [remote_max_date, max(source_dates)]))
        for row in page_rows:
            if row.get("is_fiz") != 1:
                continue
            physical_rows += 1
            tradedate = str(row.get("tradedate") or "")[:10]
            long_value = row.get("open_position_long")
            short_value = row.get("open_position_short")
            if not parse_iso_day(tradedate) or long_value is None or short_value is None:
                continue
            long_value, short_value = int(long_value), int(short_value)
            by_date[tradedate] = {
                "d": tradedate,
                "long": long_value,
                "short": short_value,
                "net": long_value - short_value,
                "gross": long_value + short_value,
                "persons_long": int(row.get("persons_long") or 0),
                "persons_short": int(row.get("persons_short") or 0),
            }

        cursor = _cursor(payload)
        if not cursor:
            break  # The real openpositions endpoint currently returns the requested range in one block.
        index = int(cursor.get("index") or start)
        total = int(cursor.get("total") or 0)
        page_size = int(cursor.get("pagesize") or len(page_rows))
        if page_size <= 0:
            raise PaginationIncomplete("cursor page size is zero")
        next_start = index + page_size
        if next_start >= total:
            break
        if next_start <= start:
            raise PaginationIncomplete(f"cursor made no progress: {start}->{next_start}")
        start = next_start
        time.sleep(PAUSE)
    else:
        raise PaginationIncomplete(f"pagination exceeded MAX_PAGES={MAX_PAGES}")

    rows = [by_date[key] for key in sorted(by_date)]
    if not rows:
        raise RemoteEmpty("MOEX returned no physical-person observations")
    return PositionRows(rows, diagnostics={
        "pages_fetched": pages,
        "raw_rows": raw_rows,
        "physical_person_rows": physical_rows,
        "first_source_date": first_source_date,
        "remote_max_date": remote_max_date,
        "output_max_date": rows[-1]["d"],
        "complete": True,
    })


def validate_rows(rows: list[dict], *, now: datetime) -> None:
    previous = ""
    seen: set[str] = set()
    today = now.astimezone(MOSCOW).date()
    for row in rows:
        day = str(row.get("d") or "")[:10]
        parsed = parse_iso_day(day)
        if not parsed:
            raise ValidationError(f"invalid date: {day!r}")
        if day in seen:
            raise ValidationError(f"duplicate date: {day}")
        if previous and day <= previous:
            raise ValidationError("dates are not strictly ascending")
        if parsed > today:
            raise ValidationError(f"future observation: {day}")
        long_value, short_value = row.get("long"), row.get("short")
        if not isinstance(long_value, int) or not isinstance(short_value, int):
            raise ValidationError(f"non-integer position at {day}")
        if long_value < 0 or short_value < 0:
            raise ValidationError(f"negative side at {day}")
        if row.get("net") != long_value - short_value:
            raise ValidationError(f"net != long - short at {day}")
        seen.add(day)
        previous = day


def rows_from_entry(entry: dict | None) -> list[dict]:
    """Read the v2 compact arrays; legacy net-only stock rows are not merge-safe."""
    if not entry:
        return []
    dates = entry.get("dates") or []
    longs = entry.get("long") or []
    shorts = entry.get("short") or []
    if not (len(dates) == len(longs) == len(shorts)):
        return []
    persons_long = entry.get("persons_long") or []
    persons_short = entry.get("persons_short") or []
    out = []
    for index, tradedate in enumerate(dates):
        long_value, short_value = longs[index], shorts[index]
        if not isinstance(long_value, int) or not isinstance(short_value, int):
            return []
        out.append({
            "d": str(tradedate)[:10],
            "long": long_value,
            "short": short_value,
            "net": long_value - short_value,
            "gross": long_value + short_value,
            "persons_long": int(persons_long[index]) if index < len(persons_long) else 0,
            "persons_short": int(persons_short[index]) if index < len(persons_short) else 0,
        })
    return out


def merge_rows(previous: list[dict], incoming: list[dict]) -> list[dict]:
    """Latest source row wins for the same date; output is unique and ascending."""
    merged = {row["d"]: dict(row) for row in previous}
    merged.update({row["d"]: dict(row) for row in incoming})
    return [merged[key] for key in sorted(merged)]


def z_score(values: list[float]) -> float | None:
    window = values[-Z_WINDOW:]
    if len(window) < MIN_POINTS:
        return None
    mean = sum(window) / len(window)
    variance = sum((value - mean) ** 2 for value in window) / (len(window) - 1)
    stddev = variance**0.5
    return round((window[-1] - mean) / stddev, 2) if stddev else None


def percentile(values: list[float]) -> float | None:
    window = values[-Z_WINDOW:]
    if len(window) < MIN_POINTS:
        return None
    last = window[-1]
    return round(100 * sum(value < last for value in window) / (len(window) - 1), 1)


def value_of(row: dict, key: str) -> int:
    if key == "gross":
        return row["long"] + row["short"]
    if key == "net":
        return row["long"] - row["short"]
    return int(row[key])


def change_over(rows: list[dict], days: int, key: str = "net") -> int | None:
    if len(rows) <= days:
        return None
    return value_of(rows[-1], key) - value_of(rows[-1 - days], key)


def robust_z(values: list[float]) -> float | None:
    window = [value for value in values[-Z_WINDOW:] if value is not None]
    if len(window) < MIN_POINTS:
        return None
    ordered = sorted(window)

    def median(sequence: list[float]) -> float:
        middle = len(sequence) // 2
        return sequence[middle] if len(sequence) % 2 else (sequence[middle - 1] + sequence[middle]) / 2

    centre = median(ordered)
    mad = median(sorted(abs(value - centre) for value in window))
    return round((window[-1] - centre) / (1.4826 * mad), 2) if mad > 0 else None


def change_series(rows: list[dict], days: int, key: str) -> list[float]:
    return [value_of(rows[index], key) - value_of(rows[index - days], key)
            for index in range(days, len(rows))]


def summarize(rows: list[dict], multiplier: float | None, price: float | None) -> dict:
    last = rows[-1]
    net_values = [float(row["net"]) for row in rows]
    gross = last["long"] + last["short"]
    summary = {
        "as_of": last["d"],
        "long": last["long"],
        "short": last["short"],
        "net": last["net"],
        "gross": gross,
        "persons_long": last["persons_long"],
        "persons_short": last["persons_short"],
        "long_share": round(last["long"] / gross, 4) if gross else None,
        "net_ratio": round(last["net"] / gross, 4) if gross else None,
        "z": z_score(net_values),
        "percentile": percentile(net_values),
        "change_1d": change_over(rows, 1),
        "change_5d": change_over(rows, 5),
        "change_20d": change_over(rows, 20),
        "long_change_1d": change_over(rows, 1, "long"),
        "long_change_5d": change_over(rows, 5, "long"),
        "long_change_20d": change_over(rows, 20, "long"),
        "short_change_1d": change_over(rows, 1, "short"),
        "short_change_5d": change_over(rows, 5, "short"),
        "short_change_20d": change_over(rows, 20, "short"),
        "gross_change_1d": change_over(rows, 1, "gross"),
        "gross_change_5d": change_over(rows, 5, "gross"),
        "gross_change_20d": change_over(rows, 20, "gross"),
        "persons_long_change_5d": change_over(rows, 5, "persons_long"),
        "persons_short_change_5d": change_over(rows, 5, "persons_short"),
        "net_change_5d_robust_z": robust_z(change_series(rows, 5, "net")),
        "gross_change_5d_robust_z": robust_z(change_series(rows, 5, "gross")),
        "min": min(row["net"] for row in rows),
        "max": max(row["net"] for row in rows),
        "points": len(rows),
    }
    if multiplier and price:
        summary.update({
            "price": price,
            "multiplier": multiplier,
            "net_rub": round(last["net"] * price * multiplier, 0),
            "long_rub": round(last["long"] * price * multiplier, 0),
            "short_rub": round(last["short"] * price * multiplier, 0),
        })
    return summary


def contract_multiplier(secid: str) -> tuple[float | None, dict]:
    payload = http_json(
        f"{ISS}/engines/futures/markets/forts/securities/{secid}.json"
        "?iss.meta=off&iss.only=securities"
    )
    rows = rows_of(payload, "securities")
    if not rows:
        return None, {}
    spec = rows[0]
    min_step, step_price = spec.get("MINSTEP"), spec.get("STEPPRICE")
    if not min_step or not step_price:
        return None, spec
    return float(step_price) / float(min_step), spec


def price_history(secid: str, date_to: str, date_from: str = HISTORY_FROM) -> dict[str, float]:
    out: dict[str, float] = {}
    start = 0
    for _ in range(200):
        payload = http_json(
            f"{ISS}/history/engines/futures/markets/forts/securities/{secid}.json"
            "?iss.meta=off&iss.only=history"
            "&history.columns=TRADEDATE,CLOSE,SETTLEPRICE"
            f"&from={date_from}&till={date_to}&start={start}"
        )
        rows = rows_of(payload, "history")
        if not rows:
            break
        for row in rows:
            value = row.get("CLOSE") or row.get("SETTLEPRICE")
            if row.get("TRADEDATE") and value:
                out[str(row["TRADEDATE"])[:10]] = float(value)
        if len(rows) < 100:
            break
        start += len(rows)
        time.sleep(PAUSE)
    return out


def shares_by_emitent() -> dict[str, str]:
    """Map issuer id to its traded common share SECID."""
    out: dict[str, str] = {}
    start = 0
    while True:
        payload = http_json(
            f"{ISS}/securities.json?iss.meta=off&engine=stock&market=shares&start={start}"
        )
        rows = rows_of(payload, "securities")
        if not rows:
            break
        for row in rows:
            if (row.get("type") == "common_share" and row.get("primary_boardid") == "TQBR"
                    and row.get("is_traded") and row.get("emitent_id") and row.get("secid")):
                out.setdefault(str(row["emitent_id"]), str(row["secid"]))
        if len(rows) < 100:
            break
        start += len(rows)
        time.sleep(PAUSE)
    return out


def futures_catalog(now: datetime) -> dict[str, dict]:
    """Resolve one currently liquid contract per ASSETCODE.

    Open interest is the primary liquidity measure, then traded value, volume
    and number of trades. Expired contracts are excluded. If MOEX has not yet
    populated market data for newly listed contracts, the nearest expiry wins.
    """
    payload = http_json(f"{ISS}/engines/futures/markets/forts/securities.json?iss.meta=off")
    market_rows = {str(row.get("SECID")): row for row in rows_of(payload, "marketdata")}
    today = now.astimezone(MOSCOW).date()
    grouped: dict[str, list[dict]] = {}
    for spec in rows_of(payload, "securities"):
        asset, secid = str(spec.get("ASSETCODE") or ""), str(spec.get("SECID") or "")
        expiry = parse_iso_day(spec.get("LASTTRADEDATE"))
        if not asset or not secid or not expiry or expiry < today:
            continue
        market = market_rows.get(secid, {})
        candidate = {
            "asset": asset,
            "secid": secid,
            "shortname": spec.get("SHORTNAME"),
            "expiration": expiry.isoformat(),
            "trading_status": "trading" if market else "listed",
            "open_interest": int(market.get("OPENPOSITION") or spec.get("PREVOPENPOSITION") or 0),
            "value_today": float(market.get("VALTODAY") or 0),
            "volume_today": int(market.get("VOLTODAY") or 0),
            "trades_today": int(market.get("NUMTRADES") or 0),
            "market_date": str(market.get("TRADEDATE") or "")[:10] or None,
            "min_step": spec.get("MINSTEP"),
            "step_price": spec.get("STEPPRICE"),
        }
        grouped.setdefault(asset, []).append(candidate)

    selected: dict[str, dict] = {}
    for asset, candidates in grouped.items():
        active = [item for item in candidates if item["open_interest"] > 0]
        if active:
            chosen = max(active, key=lambda item: (
                item["open_interest"], item["value_today"], item["volume_today"],
                item["trades_today"], -parse_iso_day(item["expiration"]).toordinal(),
            ))
        else:
            chosen = min(candidates, key=lambda item: parse_iso_day(item["expiration"]))
        selected[asset] = chosen
    return selected


def _contract_description(secid: str) -> dict[str, object]:
    payload = http_json(f"{ISS}/securities/{secid}.json?iss.meta=off&iss.only=description")
    return {str(row.get("name")): row.get("value") for row in rows_of(payload, "description")}


def equity_assets(catalog: dict[str, dict], existing: dict | None = None) -> tuple[dict[str, dict], list[str]]:
    """Map common-share ticker to underlying and the current liquid futures contract."""
    existing_tickers = (existing or {}).get("tickers") or {}
    existing_by_asset = {
        str(entry.get("underlying") or entry.get("asset")): ticker
        for ticker, entry in existing_tickers.items()
        if entry.get("underlying") or entry.get("asset")
    }
    by_emitent: dict[str, str] | None = None
    candidates: dict[str, list[dict]] = {}
    errors: list[str] = []

    for asset, contract in sorted(catalog.items()):
        if asset in INDEX_ASSETS:
            continue
        ticker = existing_by_asset.get(asset)
        name = str(contract.get("shortname") or "")
        if not ticker:
            try:
                description = _contract_description(contract["secid"])
                time.sleep(PAUSE)
            except IssError as exc:
                errors.append(f"{asset}: description: {exc}")
                continue
            if description.get("GROUPTYPE") != "Акции":
                continue
            if by_emitent is None:
                by_emitent = shares_by_emitent()
            ticker = by_emitent.get(str(description.get("EMITTER_ID")))
            if not ticker:
                continue
            name = str(description.get("CONTRACTNAME") or name)
        item = {**contract, "mapping_source": "MOEX emitter_id"}
        previous_asset = (existing_tickers.get(ticker) or {}).get("underlying") \
            or (existing_tickers.get(ticker) or {}).get("asset")
        lower_name = name.lower()
        is_perpetual = ("автопролонгац" in lower_name or "однодневн" in lower_name
                        or contract.get("expiration") == "2100-01-01")
        # Keep the established underlying methodology when it remains listed. A
        # perpetual daily contract is a different ASSETCODE and therefore a
        # different openpositions history, not a rollover of the quarterly row.
        item["_rank"] = (
            is_perpetual,
            bool(previous_asset) and asset != previous_asset,
            "привилегированн" in lower_name,
            "мини" in lower_name,
            asset,
        )
        candidates.setdefault(ticker, []).append(item)

    resolved: dict[str, dict] = {}
    for ticker, items in candidates.items():
        chosen = min(items, key=lambda item: item["_rank"])
        chosen.pop("_rank", None)
        resolved[ticker] = chosen

    # A temporary description failure must not erase a trusted asset mapping.
    for ticker, old in existing_tickers.items():
        if ticker in resolved:
            continue
        asset = old.get("underlying") or old.get("asset")
        if asset in catalog:
            resolved[ticker] = {**catalog[asset], "mapping_source": "last_good emitter mapping"}
    return resolved, errors


def moex_trading_dates(now: datetime) -> list[str]:
    """Completed MOEX sessions, ending no later than the previous Moscow day."""
    cutoff = now.astimezone(MOSCOW).date() - timedelta(days=1)
    start = cutoff - timedelta(days=35)
    payload = http_json(
        f"{ISS}/history/engines/stock/markets/index/securities/IMOEX.json"
        "?iss.meta=off&iss.only=history&history.columns=TRADEDATE"
        f"&from={start.isoformat()}&till={cutoff.isoformat()}"
    )
    dates = sorted({str(row.get("TRADEDATE"))[:10] for row in rows_of(payload, "history")
                    if parse_iso_day(row.get("TRADEDATE"))})
    if not dates:
        raise IssError("MOEX trading calendar returned no completed sessions")
    return dates


def freshness_status(latest: str | None, trading_dates: list[str]) -> tuple[str, int | None]:
    if not latest or not trading_dates:
        return "unavailable", None
    expected = trading_dates[-1]
    if latest >= expected:
        return "fresh", 0
    later = sum(day > latest for day in trading_dates)
    return ("delayed_by_exchange" if later == 1 else "stale"), later


def _series_fields(rows: list[dict]) -> dict:
    kept = rows[-HISTORY_KEEP:]
    return {
        "dates": [row["d"] for row in kept],
        "long": [row["long"] for row in kept],
        "short": [row["short"] for row in kept],
        "net": [row["net"] for row in kept],
        "persons_long": [row["persons_long"] for row in kept],
        "persons_short": [row["persons_short"] for row in kept],
    }


def _fallback_entry(existing: dict | None, contract: dict, reason: str, now: datetime,
                    *, unavailable: bool = False, failure_status: str = "remote_error",
                    expected_date: str | None = None,
                    trading_dates: list[str] | None = None) -> dict:
    entry = dict(existing or {})
    latest = (entry.get("summary") or {}).get("as_of") or (entry.get("dates") or [None])[-1]
    fallback_used = bool(entry.get("dates"))
    _old_status, lag = freshness_status(latest, trading_dates or [])
    entry.update({
        "asset": contract.get("asset") or entry.get("asset"),
        "underlying": contract.get("asset") or entry.get("underlying") or entry.get("asset"),
        "current_futures": contract.get("secid") or entry.get("current_futures") or entry.get("secid"),
        "secid": contract.get("secid") or entry.get("secid"),
        "expiration": contract.get("expiration") or entry.get("expiration"),
        "status": "unavailable" if unavailable or not fallback_used else "stale",
        "source_status": "last_good" if fallback_used else failure_status,
        "update_status": "unavailable" if unavailable else "failed",
        "freshness_status": "cache_fallback" if fallback_used else (
            "unavailable" if unavailable else failure_status
        ),
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "remote_max_date": None,
        "data_asof": latest,
        "latest_observation_date": latest,
        "expected_trading_date": expected_date,
        "lag_trading_sessions": lag if expected_date else None,
        "fallback_used": fallback_used,
        "fallback_reason": reason[:300],
        "reason": reason[:300],
    })
    if failure_status == "remote_incomplete":
        entry["pagination"] = {"complete": False, "error": reason[:240]}
    return entry


def _build_entry(*, contract: dict, existing: dict | None, trading_dates: list[str],
                 now: datetime, full_refresh: bool, index_prices: bool = False) -> dict:
    previous = [] if full_refresh else rows_from_entry(existing)
    date_from = HISTORY_FROM if not previous else previous[-1]["d"]
    asset = contract["asset"]
    try:
        incoming = positions(asset, now.astimezone(MOSCOW).date().isoformat(), date_from)
        pagination = getattr(incoming, "diagnostics", {
            "pages_fetched": 1, "raw_rows": len(incoming),
            "physical_person_rows": len(incoming),
            "first_source_date": incoming[0]["d"] if incoming else None,
            "remote_max_date": incoming[-1]["d"] if incoming else None,
            "output_max_date": incoming[-1]["d"] if incoming else None,
            "complete": True,
        })
        validate_rows(incoming, now=now)
        merged = merge_rows(previous, incoming)
        validate_rows(merged, now=now)
    except (IssError, ValidationError, KeyError, TypeError, ValueError) as exc:
        failure_status = "remote_empty" if isinstance(exc, RemoteEmpty) else (
            "remote_incomplete" if isinstance(exc, ValidationError) else "remote_error"
        )
        return _fallback_entry(
            existing, contract, str(exc), now, failure_status=failure_status,
            expected_date=trading_dates[-1] if trading_dates else None,
            trading_dates=trading_dates,
        )

    latest = merged[-1]["d"]
    status, lag = freshness_status(latest, trading_dates)
    freshness = status if trading_dates else "remote_error"
    legacy_status = status if trading_dates else "stale"
    analysis_ready = len(merged) >= MIN_POINTS
    old_latest = (existing or {}).get("latest_observation_date") \
        or ((existing or {}).get("summary") or {}).get("as_of")
    entry = {
        "asset": asset,
        "underlying": asset,
        "current_futures": contract["secid"],
        "secid": contract["secid"],
        "expiration": contract.get("expiration"),
        "trading_status": contract.get("trading_status"),
        "open_interest": contract.get("open_interest"),
        "liquidity": {
            "value_today": contract.get("value_today"),
            "volume_today": contract.get("volume_today"),
            "trades_today": contract.get("trades_today"),
        },
        "mapping_source": contract.get("mapping_source", "MOEX ASSETCODE"),
        "source": "MOEX ISS openpositions",
        "source_url": f"{ISS}/{SOURCE_PATH}/{asset}.json",
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "remote_max_date": pagination.get("remote_max_date"),
        "data_asof": latest,
        "latest_observation_date": latest,
        "expected_trading_date": trading_dates[-1] if trading_dates else None,
        "lag_trading_sessions": lag,
        "status": legacy_status if analysis_ready else "unavailable",
        "freshness_status": freshness,
        "source_status": "live",
        "update_status": "updated" if full_refresh or latest != old_latest else "unchanged",
        "fallback_used": False,
        "fallback_reason": None,
        "analysis_ready": analysis_ready,
        "history_points": len(merged),
        "history_points_required": MIN_POINTS,
        "pagination": pagination,
        "summary": summarize(merged, None, None),
        **_series_fields(merged),
    }
    if not analysis_ready:
        entry["reason"] = (
            f"live source is complete, but only {len(merged)} observations are available; "
            f"{MIN_POINTS} are required for positioning statistics"
        )
    if index_prices:
        try:
            multiplier, _spec = contract_multiplier(contract["secid"])
            price_from = entry["dates"][0]
            prices = price_history(contract["secid"], latest, price_from) if multiplier else {}
            price = prices.get(latest)
            entry["summary"] = summarize(merged, multiplier, price)
            if multiplier:
                entry["multiplier"] = multiplier
                entry["net_rub"] = [
                    round(net * prices[day] * multiplier, 0) if day in prices else None
                    for day, net in zip(entry["dates"], entry["net"])
                ]
        except IssError as exc:
            entry["notional_warning"] = str(exc)[:300]
    return entry


def load_existing(path: Path = OUT) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _payload_date(payload: dict) -> str:
    return str((payload.get("meta") or {}).get("as_of") or "")[:10]


def load_best_existing(output: Path, published: Path | None = None) -> tuple[dict, str]:
    choices = []
    for path, label, priority in ((output, "tracked_bootstrap", 0),
                                  (published, "gh_pages_last_good", 1)):
        if path is None:
            continue
        payload = load_existing(path)
        if payload:
            choices.append((_payload_date(payload), priority, payload, label))
    if not choices:
        return {}, "none"
    _day, _priority, payload, label = max(choices, key=lambda item: (item[0], item[1]))
    return payload, label


def build(today: datetime | None = None, *, existing: dict | None = None,
          full_refresh: bool = False) -> dict:
    now = utc_now(today)
    existing = existing or {}
    calendar_error = ""
    try:
        trading_dates = moex_trading_dates(now)
    except IssError as exc:
        trading_dates = []
        calendar_error = str(exc)
        log(f"calendar unavailable; expected date and lag are unknown: {exc}")
    expected = trading_dates[-1] if trading_dates else None

    catalog_error = ""
    try:
        catalog = futures_catalog(now)
    except IssError as exc:
        if not existing:
            raise
        catalog_error = str(exc)
        log(f"contract catalog unavailable; using last-good mappings: {exc}")
        catalog = {}
        for section in ("indices", "tickers"):
            for entry in (existing.get(section) or {}).values():
                asset = entry.get("underlying") or entry.get("asset")
                secid = entry.get("current_futures") or entry.get("secid")
                if asset and secid:
                    catalog.setdefault(asset, {
                        "asset": asset, "secid": secid, "expiration": entry.get("expiration"),
                        "trading_status": "unknown", "open_interest": entry.get("open_interest", 0),
                        "value_today": 0, "volume_today": 0, "trades_today": 0,
                        "mapping_source": "last_good catalog",
                    })

    mapping_error = ""
    try:
        resolved, discovery_errors = equity_assets(catalog, existing)
    except IssError as exc:
        if not existing:
            raise
        mapping_error = str(exc)
        discovery_errors = [f"issuer mapping unavailable; using last-good lineage: {exc}"]
        resolved = {}
        for ticker, old in (existing.get("tickers") or {}).items():
            asset = old.get("underlying") or old.get("asset")
            if asset in catalog:
                resolved[ticker] = {
                    **catalog[asset],
                    "mapping_source": "last_good emitter mapping",
                }
        log(f"issuer mapping unavailable; using last-good mappings: {exc}")
    validation_warning = mapping_error or catalog_error or calendar_error
    log(f"universe={len(resolved)} expected_date={expected or '-'} mode={'full' if full_refresh else 'incremental'}")
    for error in discovery_errors:
        log(f"mapping_warning={error}")

    tickers: dict[str, dict] = {}
    for ticker, contract in sorted(resolved.items()):
        entry = _build_entry(
            contract=contract,
            existing=(existing.get("tickers") or {}).get(ticker),
            trading_dates=trading_dates,
            now=now,
            full_refresh=full_refresh,
        )
        if validation_warning:
            entry["validation_warning"] = validation_warning[:300]
            entry["reason"] = "; ".join(filter(None, [
                "contract/calendar/mapping validation unavailable: " + validation_warning[:240],
                entry.get("reason"),
            ]))
        tickers[ticker] = entry
        log(f"{ticker} -> {entry.get('current_futures') or '-'} asset={entry.get('asset') or '-'} "
            f"latest={entry.get('latest_observation_date') or '-'} expected={expected or '-'} "
            f"status={entry.get('freshness_status')} update={entry.get('update_status')}")
        time.sleep(PAUSE)

    # Previously supported symbols are explicit when their active contract disappears.
    for ticker, old in sorted((existing.get("tickers") or {}).items()):
        if ticker not in tickers:
            tickers[ticker] = _fallback_entry(
                old, {"asset": old.get("asset")}, "active MOEX equity futures not resolved", now,
                unavailable=True, expected_date=expected, trading_dates=trading_dates,
            )

    indices: dict[str, dict] = {}
    for asset, title in INDEX_ASSETS.items():
        contract = catalog.get(asset)
        old = (existing.get("indices") or {}).get(asset)
        if not contract:
            indices[asset] = _fallback_entry(
                old, {"asset": asset}, "active MOEX index futures not resolved", now,
                unavailable=not bool(old), expected_date=expected, trading_dates=trading_dates,
            )
            indices[asset]["title"] = title
            continue
        entry = _build_entry(
            contract=contract,
            existing=old,
            trading_dates=trading_dates,
            now=now,
            full_refresh=full_refresh,
            index_prices=(asset == "IMOEX"),
        )
        if validation_warning:
            entry["validation_warning"] = validation_warning[:300]
            entry["reason"] = "; ".join(filter(None, [
                "contract/calendar/mapping validation unavailable: " + validation_warning[:240],
                entry.get("reason"),
            ]))
        entry["title"] = title
        indices[asset] = entry

    all_entries = list(tickers.values()) + list(indices.values())
    counts = {key: sum(entry.get("status") == key for entry in all_entries)
              for key in ("fresh", "delayed_by_exchange", "stale", "unavailable")}
    update_counts = {key: sum(entry.get("update_status") == key for entry in all_entries)
                     for key in ("updated", "unchanged", "failed", "unavailable")}
    freshness_counts = {key: sum(entry.get("freshness_status") == key for entry in all_entries)
                        for key in ("fresh", "delayed_by_exchange", "stale", "remote_error",
                                    "remote_empty", "remote_incomplete", "cache_fallback", "unavailable")}
    successful = [entry for entry in all_entries if entry.get("latest_observation_date")]
    as_of = max((entry["latest_observation_date"] for entry in successful), default=None)
    payload = {
        "meta": {
            "schema_version": 3,
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": f"MOEX ISS, {SOURCE_PATH}",
            "client_group": "FIZ — физические лица",
            "as_of": as_of,
            "latest_source_date": as_of,
            "expected_trading_date": expected,
            "calendar_status": "available" if trading_dates else "unavailable",
            "freshness_policy": "0 sessions=fresh; 1=delayed_by_exchange; >1=stale",
            "unit": "contracts",
            "net_formula": "long_phys - short_phys",
            "rollover": "openpositions is aggregated by ASSETCODE; active contract is audit metadata only",
            "no_cross_series_sum": "Position contracts from different ASSETCODE values are never summed.",
            "no_oi_share": "Open-interest share is not published because the denominator is not in this source.",
            "mode": "full_refresh" if full_refresh else "incremental",
            "tickers_total": len(tickers),
            "indices_total": len(indices),
            "tickers_ok": sum(entry.get("status") in USABLE_STATUSES for entry in tickers.values()),
            "indices_ok": sum(entry.get("status") in USABLE_STATUSES for entry in indices.values()),
            "status_counts": counts,
            "freshness_status_counts": freshness_counts,
            "update_counts": update_counts,
            "mapping_errors": len(discovery_errors),
            "calendar_error": calendar_error or None,
            "catalog_error": catalog_error or None,
        },
        "indices": indices,
        "tickers": tickers,
    }
    validate_payload(payload, now=now)
    return payload


def validate_payload(payload: dict, *, now: datetime) -> None:
    if payload.get("meta", {}).get("schema_version") not in {2, 3}:
        raise ValidationError("unexpected schema version")
    for section in ("indices", "tickers"):
        for key, entry in (payload.get(section) or {}).items():
            if entry.get("status") not in {"fresh", "delayed_by_exchange", "stale", "unavailable"}:
                raise ValidationError(f"{key}: invalid status")
            rows = rows_from_entry(entry)
            if rows:
                validate_rows(rows, now=now)
                latest = entry.get("latest_observation_date")
                if latest != rows[-1]["d"] or (entry.get("summary") or {}).get("as_of") != latest:
                    raise ValidationError(f"{key}: inconsistent latest date")
                if entry.get("underlying") != entry.get("asset"):
                    raise ValidationError(f"{key}: underlying mismatch")
                if entry.get("data_asof") not in (None, latest):
                    raise ValidationError(f"{key}: data_asof mismatch")
                pagination = entry.get("pagination") or {}
                if entry.get("source_status") == "live" and pagination.get("complete") is not True:
                    raise ValidationError(f"{key}: live pagination not complete")
            if payload.get("meta", {}).get("calendar_status") == "unavailable":
                if entry.get("expected_trading_date") is not None or entry.get("lag_trading_sessions") is not None:
                    raise ValidationError(f"{key}: calendar unavailable but expected/lag asserted")
            for value in _walk_values(entry):
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValidationError(f"{key}: non-finite value")


def strict_failures(payload: dict, *, now: datetime | None = None) -> list[str]:
    now = utc_now(now)
    failures: list[str] = []
    try:
        validate_payload(payload, now=now)
    except ValidationError as exc:
        failures.append(str(exc))
        return failures
    entries = list((payload.get("tickers") or {}).values()) + list((payload.get("indices") or {}).values())
    failed = sum(entry.get("update_status") == "failed" for entry in entries)
    if entries and failed >= max(3, math.ceil(len(entries) * 0.8)):
        failures.append(f"mass source failure: {failed}/{len(entries)} assets")
    incomplete = [entry.get("asset") for entry in entries
                  if (entry.get("pagination") or {}).get("complete") is False]
    if incomplete:
        failures.append("incomplete pagination: " + ",".join(filter(None, incomplete[:8])))
    imoex = ((payload.get("indices") or {}).get("IMOEX") or {})
    if imoex.get("source_status") != "live":
        lag = imoex.get("lag_trading_sessions")
        if lag is None or lag > 1:
            failures.append("IMOEX critical source is not live within SLA")
    return failures


def _walk_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


def atomic_write(payload: dict, path: Path = OUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def print_audit(payload: dict) -> None:
    order = {"stale": 0, "unavailable": 1, "delayed_by_exchange": 2, "fresh": 3}
    rows = []
    for ticker, entry in (payload.get("tickers") or {}).items():
        rows.append((order.get(entry.get("status"), -1), ticker, entry))
    print("ticker | futures | last_date | lag | status")
    for _rank, ticker, entry in sorted(rows):
        print(f"{ticker} | {entry.get('current_futures') or '-'} | "
              f"{entry.get('latest_observation_date') or '-'} | "
              f"{entry.get('lag_trading_sessions') if entry.get('lag_trading_sessions') is not None else '-'} | "
              f"{entry.get('status')}")
    imoex = ((payload.get("indices") or {}).get("IMOEX") or {})
    pagination = imoex.get("pagination") or {}
    print("FUTOI IMOEX | "
          f"pages={pagination.get('pages_fetched', '-')} "
          f"remote_max={imoex.get('remote_max_date') or '-'} "
          f"artifact_max={imoex.get('data_asof') or '-'} "
          f"expected={imoex.get('expected_trading_date') or '-'} "
          f"freshness={imoex.get('freshness_status') or '-'} "
          f"source={imoex.get('source_status') or '-'} "
          f"fallback={str(bool(imoex.get('fallback_used'))).lower()}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-refresh", action="store_true", help="ignore local history and rebuild")
    parser.add_argument("--audit", action="store_true", help="print full-universe freshness table")
    parser.add_argument("--strict", action="store_true", help="fail CI on unhealthy source/completeness")
    parser.add_argument("--validate-only", action="store_true", help="validate existing output without fetching")
    parser.add_argument("--last-good", type=Path, help="current published gh-pages artifact")
    parser.add_argument("--output", type=Path, default=OUT, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Full refresh discards cached observations, but keeps the trusted ticker →
    # ASSETCODE lineage so a rebuild cannot silently switch to a perpetual row.
    previous, previous_source = load_best_existing(args.output, args.last_good)
    if args.validate_only:
        if not previous:
            print("[POSITIONING] strict validation failed: artifact missing", file=sys.stderr)
            return 2
        failures = strict_failures(previous)
        for failure in failures:
            print(f"[POSITIONING] STRICT: {failure}", file=sys.stderr)
        return 2 if failures else 0
    log(f"last_good_source={previous_source} as_of={_payload_date(previous) or '-'}")
    try:
        payload = build(existing=previous, full_refresh=args.full_refresh)
        atomic_write(payload, args.output)
    except (IssError, ValidationError) as exc:
        print(f"[POSITIONING] build failed; last-good was not overwritten: {exc}", file=sys.stderr)
        return 1
    meta = payload["meta"]
    counts, updates = meta["status_counts"], meta["update_counts"]
    log(f"latest_source_date={meta['latest_source_date']} expected_date={meta['expected_trading_date']}")
    log(f"updated={updates['updated']} unchanged={updates['unchanged']} "
        f"unavailable={counts['unavailable']} failed={updates['failed']}")
    log(f"fresh={counts['fresh']} delayed_by_exchange={counts['delayed_by_exchange']} stale={counts['stale']}")
    if args.audit:
        print_audit(payload)
    failures = strict_failures(payload) if args.strict else []
    for failure in failures:
        print(f"[POSITIONING] STRICT: {failure}", file=sys.stderr)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
