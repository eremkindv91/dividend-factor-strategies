#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch only the dividend-calendar block in an additive gh-pages publish."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_site_status as bss  # noqa: E402

CFG = ("dividend_calendar", "Дивидендный календарь", "dividend_calendar.json",
       ["meta.generated_at"], "market", 0)


def main() -> int:
    directory = sys.argv[1] if len(sys.argv) > 1 else "."
    calendar_path = os.path.join(directory, "dividend_calendar.json")
    status_path = os.path.join(directory, "site_status.json")
    if not (os.path.exists(calendar_path) and os.path.exists(status_path)):
        sys.stderr.write("[patch-div-calendar] нет calendar/status — пропуск\n")
        return 0
    try:
        with open(calendar_path, encoding="utf-8") as handle:
            calendar = json.load(handle)
        with open(status_path, encoding="utf-8") as handle:
            status = json.load(handle)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"[patch-div-calendar] битый JSON — пропуск ({exc})\n")
        return 0
    now = datetime.now(timezone.utc)
    block = bss.classify_block(CFG, calendar, False, now)
    status.setdefault("blocks", {})["dividend_calendar"] = block
    worst = "fresh"
    for item in status["blocks"].values():
        value = item.get("freshness_status") or item.get("status", "fresh")
        if bss.SEVERITY.get(value, 0) > bss.SEVERITY.get(worst, 0):
            worst = value
    status["overall"] = bss.COARSE.get(worst, "stale")
    status["overall_status"] = worst
    status["overall_message"] = bss.overall_message(worst)
    with open(status_path, "w", encoding="utf-8") as handle:
        json.dump(status, handle, ensure_ascii=False, separators=(",", ":"))
    sys.stderr.write(f"[patch-div-calendar] block={block['freshness_status']} overall={worst}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
