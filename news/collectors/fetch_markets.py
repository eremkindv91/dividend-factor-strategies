#!/usr/bin/env python3
"""Collect public pre-market market snapshot for the morning news block."""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "news" / "artifacts"
DEFAULT_OUT = ARTIFACTS / "overnight_markets.txt"
MSK = timezone(timedelta(hours=3))
HEADERS = {"User-Agent": "dividend-factor-strategies-news/1.0"}
REQUEST_ATTEMPTS = 2


MOEX = [
    ("IMOEX", "IMOEX", "stock", "index"),
    ("RTS", "RTSI", "stock", "index"),
    ("USD/RUB", "USD000UTSTOM", "currency", "selt"),
    ("CNY/RUB", "CNYRUB_TOM", "currency", "selt"),
]
YF = [
    ("S&P 500", "^GSPC"),
    ("Nasdaq", "^IXIC"),
    ("Nikkei 225", "^N225"),
    ("Hang Seng", "^HSI"),
    ("Brent", "BZ=F"),
    ("Золото", "GC=F"),
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def fmt_num(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return ""


def fmt_pct(value: Any) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return ""
    if 0 < abs(value) < 0.005:
        return f"{value:+.4f}%"
    return f"{value:+.2f}%"


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_float(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = as_float(row.get(key))
        if value is not None:
            return value
    return None


def pct_from_absolute_change(current_value: Any, absolute_change: Any) -> str:
    current = as_float(current_value)
    change = as_float(absolute_change)
    if current is None or change is None or abs(change) < 1e-12:
        return ""
    previous = current - change
    if abs(previous) < 1e-12:
        return ""
    return fmt_pct((change / previous) * 100.0)


def moex_change_pct(row: dict[str, Any]) -> str:
    current = first_float(row, ("LAST", "CURRENTVALUE"))
    raw_pct = as_float(row.get("LASTCHANGEPRCNT"))
    if raw_pct is not None and abs(raw_pct) > 1e-12:
        return fmt_pct(raw_pct)
    absolute_change = first_float(row, ("CHANGE", "LASTCHANGE"))
    return pct_from_absolute_change(current, absolute_change)


def moex_line_from_row(name: str, row: dict[str, Any]) -> str:
    value = first_float(row, ("LAST", "CURRENTVALUE"))
    if value is None:
        return ""
    change = moex_change_pct(row)
    as_of = row.get("SYSTIME") or row.get("TIME") or datetime.now(MSK).isoformat()
    return f"{name} | {value:.2f} | {change} | {as_of}"


def market_line_from_closes(name: str, closes: list[tuple[str, float]]) -> str:
    if len(closes) < 2:
        return ""
    prev_as_of, prev = closes[-2]
    as_of, last = closes[-1]
    _ = prev_as_of
    if abs(prev) < 1e-12:
        return ""
    change = (last / prev - 1.0) * 100.0
    return f"{name} | {last:.2f} | {fmt_pct(change)} | {as_of}"


def moex_marketdata(secid: str, engine: str, market: str) -> dict[str, Any] | None:
    url = (
        f"https://iss.moex.com/iss/engines/{engine}/markets/{market}/securities/{secid}.json"
        "?iss.meta=off&iss.only=marketdata"
        "&marketdata.columns=SECID,LAST,LASTCHANGEPRCNT,CURRENTVALUE,LASTCHANGE,CHANGE,TIME,SYSTIME"
    )
    last_error: Exception | None = None
    for attempt in range(REQUEST_ATTEMPTS):
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            r.raise_for_status()
            payload = r.json()
            break
        except Exception as e:  # noqa: BLE001
            last_error = e
            if attempt + 1 < REQUEST_ATTEMPTS:
                time.sleep(0.5)
    else:
        if last_error is not None:
            raise last_error
        return None
    block = payload.get("marketdata") or {}
    cols = block.get("columns") or []
    data = block.get("data") or []
    if not data:
        return None
    row = dict(zip(cols, data[0]))
    return row


def collect_moex() -> list[str]:
    lines: list[str] = []
    for name, secid, engine, market in MOEX:
        try:
            row = moex_marketdata(secid, engine, market)
            if not row:
                continue
            line = moex_line_from_row(name, row)
            if line:
                lines.append(line)
        except Exception as e:  # noqa: BLE001
            print(f"[markets:moex] skip {name}: {e}", file=sys.stderr)
    return lines


def yahoo_chart_closes(symbol: str) -> list[tuple[str, float]]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol, safe='')}"
    last_error: Exception | None = None
    for attempt in range(REQUEST_ATTEMPTS):
        try:
            r = requests.get(
                url,
                params={"range": "7d", "interval": "1d"},
                headers={**HEADERS, "User-Agent": "Mozilla/5.0"},
                timeout=12,
            )
            r.raise_for_status()
            payload = r.json()
            break
        except Exception as e:  # noqa: BLE001
            last_error = e
            if attempt + 1 < REQUEST_ATTEMPTS:
                time.sleep(0.5)
    else:
        if last_error is not None:
            raise last_error
        return []
    result = ((payload.get("chart") or {}).get("result") or [])
    if not result:
        return []
    first = result[0]
    timestamps = first.get("timestamp") or []
    quote = (((first.get("indicators") or {}).get("quote") or [{}])[0])
    closes = quote.get("close") or []
    rows: list[tuple[str, float]] = []
    for ts, close in zip(timestamps, closes):
        value = as_float(close)
        if value is None:
            continue
        as_of = datetime.fromtimestamp(float(ts), timezone.utc).date().isoformat()
        rows.append((as_of, value))
    return rows


def collect_yfinance() -> list[str]:
    lines: list[str] = []
    for name, symbol in YF:
        try:
            line = market_line_from_closes(name, yahoo_chart_closes(symbol))
            if line:
                lines.append(line)
        except Exception as e:  # noqa: BLE001
            print(f"[markets:yahoo] skip {name}: {e}", file=sys.stderr)
    return lines


def main() -> int:
    args = parse_args()
    lines = collect_yfinance() + collect_moex()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print(f"[markets] wrote {args.output} rows={len(lines)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
