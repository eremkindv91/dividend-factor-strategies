#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Собрать site/marketsaw_manifest.json из уже сгенерированных файлов контуров + зеркало MCFTR.

Читает site/marketsaw.json (MCFTR, канонический — генерит build_marketsaw.py) и
site/marketsaw_imoex.json (генерит build_imoex_saw.py), пишет:
  - marketsaw_mcftr.json — идентичная копия marketsaw.json (новое имя для двухконтурного фронта;
    marketsaw.json остаётся для обратной совместимости существующих читателей);
  - marketsaw_manifest.json — какой контур доступен/свеж/fallback, default_index=MCFTR.

Сбой/отсутствие одного контура НЕ роняет другой — manifest честно отражает состояние
(available/fallback per-индекс). Чистый stdlib. Выход всегда 0 (витрина, не гейт).

CLI:  python market_saw/production/build_market_saw_manifest.py [site_dir]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "scripts"))
try:
    import trading_calendar as tc  # noqa: E402
except Exception:  # noqa: BLE001
    tc = None

SCHEMA = 2
DEFAULT_INDEX = "MCFTR"


def _load(path: str):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return "broken"


def _freshness(data_last: str | None) -> str:
    """Свежесть контура по торговому календарю MOEX (выходной ≠ stale)."""
    if not data_last:
        return "unavailable"
    if tc is None:
        return "fresh"
    try:
        from datetime import date
        now_msk = tc.to_msk(datetime.now(timezone.utc))
        behind = tc.sessions_between(date.fromisoformat(str(data_last)[:10]), now_msk)
    except Exception:  # noqa: BLE001
        return "fresh"
    if behind <= 1:            # штатный лаг официального индекса до одной сессии
        return "market_closed_current" if not tc.is_session_open(now_msk) else "fresh"
    return "delayed" if behind == 2 else "stale"


def _index_entry(file_name: str, obj, expect_index: str) -> dict:
    if obj is None or obj == "broken":
        return {"file": file_name, "available": False, "source_as_of": None,
                "freshness_status": "unavailable", "fallback": False}
    ok_index = obj.get("index") == expect_index
    data_last = obj.get("data_last") or (obj.get("official_close") or {}).get("date")
    return {
        "file": file_name, "available": bool(ok_index),
        "source_as_of": data_last, "freshness_status": _freshness(data_last),
        "fallback": bool(obj.get("fallback", False)),
    }


def build(site_dir: str) -> dict:
    mcftr = _load(os.path.join(site_dir, "marketsaw.json"))
    imoex = _load(os.path.join(site_dir, "marketsaw_imoex.json"))

    # зеркало MCFTR под новым именем (совместимость двухконтурного фронта)
    if mcftr not in (None, "broken"):
        tmp = os.path.join(site_dir, "marketsaw_mcftr.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mcftr, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, os.path.join(site_dir, "marketsaw_mcftr.json"))

    indices = {
        "MCFTR": _index_entry("marketsaw_mcftr.json", mcftr, "MCFTR"),
        "IMOEX": _index_entry("marketsaw_imoex.json", imoex, "IMOEX"),
    }
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "default_index": DEFAULT_INDEX,
        "indices": indices,
    }


def main() -> int:
    site_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "site")
    payload = build(site_dir)
    out = os.path.join(site_dir, "marketsaw_manifest.json")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out)
    sys.stderr.write("[manifest] " + ", ".join(
        f"{k}:{v['freshness_status']}{'(fallback)' if v['fallback'] else ''}"
        for k, v in payload["indices"].items()) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
