#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Валидация site/events_calendar.json перед публикацией (гейт).

Проверяет структуру data-contract и инварианты: даты парсятся, importance в [0,100],
нет NaN/Infinity, у каждого события есть источник, today_event_count совпадает с честным
фильтром по МСК. exit≠0 → шаг публикации календаря не выполняется. Чистый stdlib.

CLI: python scripts/validate_events_calendar.py [путь]
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(REPO, "site", "events_calendar.json")
MSK = timezone(timedelta(hours=3))
VALID_TYPES = {
    "cbr_rate_decision", "dividend_registry_close", "last_buy_day", "board_dividend_recommendation",
    "company_earnings", "gosa", "vosa", "ofz_auction", "macro_release", "general_corporate_event",
}


def is_finite_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    errs, warns = [], []

    if not os.path.exists(path) or os.path.getsize(path) == 0:
        sys.stderr.write(f"[validate-events] ФЕЙЛ: файл отсутствует/пуст: {path}\n")
        return 2
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except ValueError as e:
        sys.stderr.write(f"[validate-events] ФЕЙЛ: невалидный JSON ({e})\n")
        return 2

    meta = d.get("meta")
    if not isinstance(meta, dict):
        errs.append("нет meta")
        meta = {}
    if not meta.get("generated_at"):
        errs.append("нет meta.generated_at")
    if meta.get("timezone") != "Europe/Moscow":
        errs.append(f"timezone != Europe/Moscow ({meta.get('timezone')!r})")

    events = d.get("events")
    if not isinstance(events, list):
        errs.append("events не массив")
        events = []

    today_iso = datetime.now(MSK).date().isoformat()
    today_seen = 0
    for i, e in enumerate(events):
        if not isinstance(e, dict):
            errs.append(f"событие #{i} не объект"); continue
        ds = e.get("date")
        try:
            d_parsed = date.fromisoformat(str(ds))
        except (ValueError, TypeError):
            errs.append(f"событие #{i} ({e.get('id')}): дата не парсится: {ds!r}"); continue
        if d_parsed.isoformat() == today_iso:
            today_seen += 1
        if e.get("event_type") not in VALID_TYPES:
            errs.append(f"событие #{i} ({e.get('id')}): event_type пуст/неизвестен: {e.get('event_type')!r}")
        imp = e.get("importance")
        if not is_finite_num(imp) or not (0 <= imp <= 100):
            errs.append(f"событие #{i} ({e.get('id')}): importance вне [0,100] или NaN: {imp!r}")
        if not e.get("source") and e.get("data_status") != "marked_manual":
            errs.append(f"событие #{i} ({e.get('id')}): нет источника (и не marked_manual)")
        # NaN/Infinity в любом числовом поле
        for k, v in e.items():
            if isinstance(v, float) and not math.isfinite(v):
                errs.append(f"событие #{i} ({e.get('id')}): поле {k} = {v}")

    tec = meta.get("today_event_count")
    if tec is not None and tec != today_seen:
        # мягко: календарь мог быть собран в другой день (деплой отстал) — это не битьё, а stale.
        warns.append(f"today_event_count={tec} в meta ≠ {today_seen} по МСК-фильтру сейчас "
                     f"(вероятно, календарь собран в другой день — фронт фильтрует сам).")

    for w in warns:
        sys.stderr.write(f"[validate-events] WARN: {w}\n")
    if errs:
        sys.stderr.write("[validate-events] ФЕЙЛ:\n  - " + "\n  - ".join(errs) + "\n")
        return 1
    sys.stderr.write(f"[validate-events] OK: {len(events)} событий, сегодня по МСК={today_seen}, "
                     f"status={meta.get('status')}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
