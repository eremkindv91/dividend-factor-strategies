#!/usr/bin/env python3
"""Render manually maintained news/calendar.yml into today's agenda text."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
NEWS_DIR = ROOT / "news"
ARTIFACTS = NEWS_DIR / "artifacts"
MSK = timezone(timedelta(hours=3))
DEFAULT_CALENDAR = NEWS_DIR / "calendar.yml"
DEFAULT_OUT = ARTIFACTS / "today_calendar.txt"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--calendar", type=Path, default=DEFAULT_CALENDAR)
    p.add_argument("--output", type=Path, default=DEFAULT_OUT)
    p.add_argument("--date", default=datetime.now(MSK).date().isoformat())
    return p.parse_args()


def event_date(event: dict[str, Any]) -> str:
    value = event.get("date") or event.get("day") or ""
    return str(value)[:10]


def main() -> int:
    args = parse_args()
    data: dict[str, Any] = {}
    if args.calendar.exists():
        with args.calendar.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    events = data.get("events") if isinstance(data, dict) else []
    rows = []
    for event in events or []:
        if not isinstance(event, dict) or event_date(event) != args.date:
            continue
        tm = str(event.get("time") or "-")
        title = str(event.get("event") or event.get("title") or "").strip()
        ticker = str(event.get("ticker") or "-").strip() or "-"
        if title:
            rows.append(f"{tm} | {title} | {ticker}")
    rows.sort(key=lambda x: ("99:99" if x.startswith("-") else x.split("|", 1)[0].strip(), x))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    print(f"[calendar] wrote {args.output} rows={len(rows)} date={args.date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
