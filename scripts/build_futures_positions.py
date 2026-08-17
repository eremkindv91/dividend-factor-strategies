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
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
TRANSIENT_HTTP = {408, 425, 429, 500, 502, 503, 504}
USABLE_STATUSES = {"fresh", "delayed_by_exchange", "stale"}
INDEX_ASSETS = {
    "IMOEX": "Вечный фьючерс на Индекс МосБиржи",
    "MIX": "Фьючерс на Индекс МосБиржи",
    "MXI": "Фьючерс на Индекс МосБиржи (мини)",
}


class IssError(RuntimeError):
    """MOEX did not return a usable response."""


class ValidationError(RuntimeError):
    """A source or merged series violates the public data contract."""


def tls_context() -> ssl.SSLContext:
    """Use an installed CA bundle when a local Python lacks macOS system roots."""
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


TLS_CONTEXT = tls_context()


def log(message: str) -> None:
    print(f"[POSITIONING] {message}")


def utc_now(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def http_json(url: str, tries: int = 3, timeout: int = 45) -> dict:
    """Read JSON with bounded retries for temporary transport/server errors."""
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout, context=TLS_CONTEXT) as response:
                return json.loads(response.read().decode("utf-8-sig"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in TRANSIENT_HTTP:
                raise IssError(f"HTTP {exc.code}: {url}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            last = exc
        if attempt + 1 < tries:
            time.sleep(0.6 * (2**attempt))
    raise IssError(f"{url}: {last}")


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


def positions(asset: str, date_to: str, date_from: str = HISTORY_FROM) -> list[dict]:
    """Return physical-person long/short positions for an underlying asset."""
    url = (
        f"{ISS}/{SOURCE_PATH}/{asset}.json?iss.meta=off"
        f"&from={date_from}&till={date_to}"
    )
    payload = http_json(url)
    by_date: dict[str, dict] = {}
    for row in rows_of(payload, "open_positions"):
        if row.get("is_fiz") != 1:
            continue
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
    return [by_date[key] for key in sorted(by_date)]


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
                    *, unavailable: bool = False) -> dict:
    entry = dict(existing or {})
    entry.update({
        "asset": contract.get("asset") or entry.get("asset"),
        "underlying": contract.get("asset") or entry.get("underlying") or entry.get("asset"),
        "current_futures": contract.get("secid") or entry.get("current_futures") or entry.get("secid"),
        "secid": contract.get("secid") or entry.get("secid"),
        "expiration": contract.get("expiration") or entry.get("expiration"),
        "status": "unavailable" if unavailable or not entry.get("dates") else "stale",
        "source_status": "last_good" if entry.get("dates") else "unavailable",
        "update_status": "unavailable" if unavailable else "failed",
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_observation_date": (entry.get("summary") or {}).get("as_of")
            or (entry.get("dates") or [None])[-1],
        "reason": reason[:300],
    })
    return entry


def _build_entry(*, contract: dict, existing: dict | None, trading_dates: list[str],
                 now: datetime, full_refresh: bool, index_prices: bool = False) -> dict:
    previous = [] if full_refresh else rows_from_entry(existing)
    date_from = HISTORY_FROM if not previous else previous[-1]["d"]
    asset = contract["asset"]
    try:
        incoming = positions(asset, now.astimezone(MOSCOW).date().isoformat(), date_from)
        validate_rows(incoming, now=now)
        merged = merge_rows(previous, incoming)
        validate_rows(merged, now=now)
        if len(merged) < MIN_POINTS:
            raise ValidationError(f"only {len(merged)} observations; minimum is {MIN_POINTS}")
    except (IssError, ValidationError, KeyError, TypeError, ValueError) as exc:
        return _fallback_entry(existing, contract, str(exc), now)

    latest = merged[-1]["d"]
    status, lag = freshness_status(latest, trading_dates)
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
        "latest_observation_date": latest,
        "expected_trading_date": trading_dates[-1],
        "lag_trading_sessions": lag,
        "status": status,
        "source_status": "live",
        "update_status": "updated" if full_refresh or latest != old_latest else "unchanged",
        "summary": summarize(merged, None, None),
        **_series_fields(merged),
    }
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


def build(today: datetime | None = None, *, existing: dict | None = None,
          full_refresh: bool = False) -> dict:
    now = utc_now(today)
    existing = existing or {}
    calendar_error = ""
    try:
        trading_dates = moex_trading_dates(now)
    except IssError as exc:
        previous_expected = (existing.get("meta") or {}).get("expected_trading_date")
        if not previous_expected:
            raise
        trading_dates = [previous_expected]
        calendar_error = str(exc)
        log(f"calendar unavailable; freshness will be stale: {exc}")
    expected = trading_dates[-1]

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

    resolved, discovery_errors = equity_assets(catalog, existing)
    log(f"universe={len(resolved)} expected_date={expected} mode={'full' if full_refresh else 'incremental'}")
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
        if entry.get("status") in {"fresh", "delayed_by_exchange"} and (calendar_error or catalog_error):
            entry["status"] = "stale"
            entry["reason"] = "contract/calendar validation unavailable: " + (calendar_error or catalog_error)[:240]
        tickers[ticker] = entry
        log(f"{ticker} -> {entry.get('current_futures') or '-'} asset={entry.get('asset') or '-'} "
            f"latest={entry.get('latest_observation_date') or '-'} expected={expected} "
            f"status={entry.get('status')} update={entry.get('update_status')}")
        time.sleep(PAUSE)

    # Previously supported symbols are explicit when their active contract disappears.
    for ticker, old in sorted((existing.get("tickers") or {}).items()):
        if ticker not in tickers:
            tickers[ticker] = _fallback_entry(
                old, {"asset": old.get("asset")}, "active MOEX equity futures not resolved", now,
                unavailable=True,
            )

    indices: dict[str, dict] = {}
    for asset, title in INDEX_ASSETS.items():
        contract = catalog.get(asset)
        old = (existing.get("indices") or {}).get(asset)
        if not contract:
            indices[asset] = _fallback_entry(
                old, {"asset": asset}, "active MOEX index futures not resolved", now,
                unavailable=not bool(old),
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
        if entry.get("status") in {"fresh", "delayed_by_exchange"} and (calendar_error or catalog_error):
            entry["status"] = "stale"
            entry["reason"] = "contract/calendar validation unavailable: " + (calendar_error or catalog_error)[:240]
        entry["title"] = title
        indices[asset] = entry

    all_entries = list(tickers.values()) + list(indices.values())
    counts = {key: sum(entry.get("status") == key for entry in all_entries)
              for key in ("fresh", "delayed_by_exchange", "stale", "unavailable")}
    update_counts = {key: sum(entry.get("update_status") == key for entry in all_entries)
                     for key in ("updated", "unchanged", "failed")}
    successful = [entry for entry in all_entries if entry.get("latest_observation_date")]
    as_of = max((entry["latest_observation_date"] for entry in successful), default=None)
    payload = {
        "meta": {
            "schema_version": 2,
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": f"MOEX ISS, {SOURCE_PATH}",
            "client_group": "FIZ — физические лица",
            "as_of": as_of,
            "latest_source_date": as_of,
            "expected_trading_date": expected,
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
    if payload.get("meta", {}).get("schema_version") != 2:
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
            for value in _walk_values(entry):
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValidationError(f"{key}: non-finite value")


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-refresh", action="store_true", help="ignore local history and rebuild")
    parser.add_argument("--audit", action="store_true", help="print full-universe freshness table")
    parser.add_argument("--output", type=Path, default=OUT, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Full refresh discards cached observations, but keeps the trusted ticker →
    # ASSETCODE lineage so a rebuild cannot silently switch to a perpetual row.
    previous = load_existing(args.output)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
