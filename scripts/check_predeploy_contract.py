#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 hard pre-deploy gate: пустой/битый core-JSON НЕ должен попасть на gh-pages.

Отличие от validate_site_data.py: тот трактует ОТСУТСТВУЮЩИЙ файл как warning
(«пропуск»), поэтому пустой data.json прошёл бы. Здесь — жёсткие обязательные
инварианты монетизируемого сайта: если они нарушены, exit 2 → публикация не идёт
(либо сработает restore_last_good_site_data.py и подменит битый файл last-good).

Проверяет минимальный контракт КАЖДОГО обязательного блока и печатает per-file
вердикт (OK / BROKEN + причина). Чистый stdlib.

CLI:
  python scripts/check_predeploy_contract.py            # все обязательные блоки
  python scripts/check_predeploy_contract.py --json     # + машиночитаемый итог в stdout
Коды выхода: 0 — все OK; 2 — есть BROKEN обязательный блок.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(REPO, "site")


def load(name: str):
    """(obj, error|None). Отсутствие файла — это ошибка контракта (в отличие от validate_site_data)."""
    full = os.path.join(SITE, name)
    if not os.path.exists(full):
        return None, "файл отсутствует"
    if os.path.getsize(full) == 0:
        return None, "файл пуст (0 байт)"
    try:
        with open(full, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:  # noqa: BLE001
        return None, f"невалидный JSON ({str(e)[:60]})"


def _age_days(s) -> float | None:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 86400
    except ValueError:
        try:
            return (date.today() - date.fromisoformat(str(s)[:10])).days
        except ValueError:
            return None


# ── контракты обязательных блоков (broken → блокирует публикацию) ─────────────
def check_data(d) -> str | None:
    meta, tk = d.get("meta") or {}, d.get("tickers")
    if not isinstance(tk, list) or len(tk) < 30:
        return f"tickers пуст/короткий ({len(tk) if isinstance(tk, list) else 'нет'})"
    if not meta.get("price_asof"):
        return "нет meta.price_asof"
    with_price = sum(1 for t in tk if isinstance(t.get("price"), (int, float)))
    if with_price < len(tk) * 0.5:
        return f"цены есть лишь у {with_price}/{len(tk)} тикеров (<50%)"
    return None


def check_marketsaw(d) -> str | None:
    if (d.get("index") or "").upper() != "MCFTR":
        return f"index={d.get('index')!r}, ожидался MCFTR (не IMOEX)"
    series = d.get("series") or []
    if len(series) < 100:
        return f"series слишком короткий ({len(series)})"
    if not d.get("current_phase"):
        return "нет current_phase"
    return None


# обязательные (broken → block) и опциональные (broken → только пометка)
REQUIRED = {
    "data.json": check_data,
    "marketsaw.json": check_marketsaw,
}
OPTIONAL = ["marlamov.json", "bonds/screener.json", "cbr/valuation.json", "news.json", "site_financials.json"]


def main() -> int:
    results = {}
    broken_required = []
    for name, checker in REQUIRED.items():
        obj, e = load(name)
        if e:
            results[name] = {"status": "broken", "reason": e, "required": True}
            broken_required.append(name)
            continue
        reason = checker(obj)
        results[name] = {"status": "broken" if reason else "ok", "reason": reason, "required": True}
        if reason:
            broken_required.append(name)
    for name in OPTIONAL:
        obj, e = load(name)
        results[name] = {"status": "broken" if e else "ok", "reason": e, "required": False}

    for name, r in results.items():
        tag = "OK " if r["status"] == "ok" else "BROKEN"
        req = "" if r["required"] else " (опц.)"
        sys.stdout.write(f"  [{tag}] {name}{req}{(' — ' + r['reason']) if r['reason'] else ''}\n")

    if "--json" in sys.argv:
        sys.stdout.write("PREDEPLOY_JSON:" + json.dumps(results, ensure_ascii=False) + "\n")

    if broken_required:
        sys.stderr.write(f"\n[predeploy] БЛОК ПУБЛИКАЦИИ: битые обязательные блоки: {', '.join(broken_required)}\n")
        return 2
    sys.stdout.write("[predeploy] контракт обязательных блоков OK — публикация разрешена\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
