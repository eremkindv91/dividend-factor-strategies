#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Валидатор контракта data/security_master.json (Фаза 1 Daily Market Data Foundation).

Проверяет схему и инварианты; exit≠0 → артефакт невалиден (не публиковать/не использовать
в downstream). Чистый stdlib. Используется в тестах и перед коммитом артефакта.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(REPO, "data", "security_master.json")

STATUSES = {"active", "renamed", "delisted", "historical"}
SHARE_CLASSES = {"ordinary", "preferred"}
_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")     # YYYY | YYYY-MM | YYYY-MM-DD


def _date_ok(s, today):
    """None/валидная дата не из будущего. Частичные даты (YYYY / YYYY-MM) допускаются."""
    if s is None:
        return True
    if not isinstance(s, str) or not _DATE_RE.match(s):
        return False
    yr = int(s[:4])
    return yr <= today.year + 0 if len(s) == 4 else s[:10] <= today.isoformat()


def validate(obj, today=None) -> list[str]:
    today = today or datetime.now(timezone.utc).date()
    errs = []
    if not isinstance(obj, dict):
        return ["корень не объект"]
    if obj.get("schema_version") != 1:
        errs.append(f"schema_version != 1 ({obj.get('schema_version')})")
    if not obj.get("methodology_version"):
        errs.append("нет methodology_version")
    secs = obj.get("securities")
    if not isinstance(secs, list) or not secs:
        return errs + ["securities пуст/не список"]

    seen = set()
    req = ["canonical_ticker", "secid", "board", "instrument_type", "share_class",
           "base_ticker", "previous_tickers", "successor_ticker", "corporate_action_refs", "status"]
    tickers = {s.get("canonical_ticker") for s in secs if isinstance(s, dict)}
    for s in secs:
        if not isinstance(s, dict):
            errs.append("элемент securities не объект"); continue
        ct = s.get("canonical_ticker")
        for f in req:
            if f not in s:
                errs.append(f"{ct}: нет поля {f}")
        if ct in seen:
            errs.append(f"дубль canonical_ticker: {ct}")
        seen.add(ct)
        if s.get("share_class") not in SHARE_CLASSES:
            errs.append(f"{ct}: share_class={s.get('share_class')}")
        if s.get("status") not in STATUSES:
            errs.append(f"{ct}: status={s.get('status')}")
        # преф не должен иметь тот же canonical, что его ordinary (классы не слиты)
        if s.get("share_class") == "preferred" and s.get("base_ticker") == ct:
            errs.append(f"{ct}: pref без отличного base_ticker (классы слиты?)")
        # successor должен существовать как запись (если указан)
        succ = s.get("successor_ticker")
        if succ and succ not in tickers:
            errs.append(f"{ct}: successor {succ} отсутствует в реестре")
        # даты корпдействий — валидны и не из будущего
        for r in s.get("corporate_action_refs") or []:
            if not _date_ok(r.get("date"), today):
                errs.append(f"{ct}: невалидная/будущая дата корпдействия {r.get('date')}")
        if not _date_ok(s.get("listing_end"), today):
            errs.append(f"{ct}: невалидная/будущая listing_end {s.get('listing_end')}")
    # counts согласованы
    c = obj.get("counts") or {}
    if c.get("total") not in (None, len(secs)):
        errs.append(f"counts.total={c.get('total')} != {len(secs)}")
    return errs


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    try:
        obj = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.stderr.write(f"[validate-sec-master] не читается {path}: {e}\n")
        return 2
    errs = validate(obj)
    if errs:
        sys.stderr.write("[validate-sec-master] НЕВАЛИДНО:\n  " + "\n  ".join(errs[:40]) + "\n")
        return 1
    sys.stderr.write(f"[validate-sec-master] OK: {len(obj['securities'])} бумаг\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
