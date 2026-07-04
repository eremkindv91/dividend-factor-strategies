#!/usr/bin/env python3
"""Collect public pre-market market snapshot for the morning news block."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "news" / "artifacts"
DEFAULT_OUT = ARTIFACTS / "overnight_markets.txt"
MSK = timezone(timedelta(hours=3))
HEADERS = {"User-Agent": "dividend-factor-strategies-news/1.0"}


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
    return f"{value:+.2f}%"


def moex_marketdata(secid: str, engine: str, market: str) -> dict[str, Any] | None:
    url = (
        f"https://iss.moex.com/iss/engines/{engine}/markets/{market}/securities/{secid}.json"
        "?iss.meta=off&iss.only=marketdata&marketdata.columns=SECID,LAST,LASTCHANGEPRCNT,TIME,SYSTIME"
    )
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    payload = r.json()
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
            value = fmt_num(row.get("LAST"))
            change = fmt_pct(row.get("LASTCHANGEPRCNT"))
            as_of = row.get("SYSTIME") or row.get("TIME") or datetime.now(MSK).isoformat()
            if value:
                lines.append(f"{name} | {value} | {change} | {as_of}")
        except Exception as e:  # noqa: BLE001
            print(f"[markets:moex] skip {name}: {e}", file=sys.stderr)
    return lines


def collect_yfinance() -> list[str]:
    try:
        import yfinance as yf  # type: ignore
    except Exception as e:  # noqa: BLE001
        print(f"[markets:yf] yfinance unavailable: {e}", file=sys.stderr)
        return []
    lines: list[str] = []
    for name, symbol in YF:
        try:
            hist = yf.Ticker(symbol).history(period="7d", interval="1d", auto_adjust=False)
            if hist is None or len(hist) < 2:
                continue
            closes = hist["Close"].dropna()
            if len(closes) < 2:
                continue
            last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2])
            change = (last / prev - 1.0) * 100.0
            idx = closes.index[-1]
            as_of = str(getattr(idx, "date", lambda: idx)())
            lines.append(f"{name} | {last:.2f} | {change:+.2f}% | {as_of}")
        except Exception as e:  # noqa: BLE001
            print(f"[markets:yf] skip {name}: {e}", file=sys.stderr)
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
