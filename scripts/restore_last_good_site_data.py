#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0: last-good fallback. Если свежесобранный core-файл битый/пустой, но на текущем
gh-pages (last-good) он валиден — подставляем last-good и помечаем блок как fallback.

Так один битый источник (напр. MOEX/ISS упал → пустой data.json) не роняет весь сайт:
публикуется свежее там, где получилось, и last-good там, где нет, с явной пометкой
(site/_fallback.json → build_site_status.py покажет статус «fallback»).

Использование (в update.yml после клона gh-pages в $LAST_GOOD):
  python scripts/restore_last_good_site_data.py --last-good <dir_с_gh-pages>

Чистый stdlib. Выход 0 (даже если что-то восстановили). Пишет site/_fallback.json.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(REPO, "site")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_predeploy_contract import REQUIRED, is_broken_result, load as _load  # noqa: E402

# файлы, которые имеет смысл откатывать к last-good (core + важные опциональные)
CANDIDATES = ["data.json", "marketsaw.json", "returns.json", "marlamov.json",
              "quality.json", "dividend_calendar.json", "events_calendar.json", "bonds/screener.json", "cbr/valuation.json",
              "news.json", "site_financials.json", "market_history.json",
              "futures_positions.json", "market_positioning_commentary.json"]


def is_valid(path: str) -> bool:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    try:
        with open(path, encoding="utf-8") as f:
            json.load(f)
        return True
    except Exception:  # noqa: BLE001
        return False


def fresh_broken(name: str) -> bool:
    """Свежесобранный файл битый ИЛИ (для обязательных) провалил контракт."""
    obj, e = _load(name)
    if e:
        return True
    if name in REQUIRED:
        return is_broken_result(REQUIRED[name](obj))
    return False


def main() -> int:
    last_good = None
    if "--last-good" in sys.argv:
        last_good = sys.argv[sys.argv.index("--last-good") + 1]
    if not last_good or not os.path.isdir(last_good):
        sys.stderr.write("[restore] нет каталога last-good — пропуск (первый деплой?)\n")
        _write_marker([])
        return 0

    restored = []
    for name in CANDIDATES:
        if not fresh_broken(name):
            continue
        src = os.path.join(last_good, name)
        if not is_valid(src):
            sys.stderr.write(f"[restore] {name} битый, но и last-good невалиден — оставляю как есть\n")
            continue
        dst = os.path.join(SITE, name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        restored.append(name)
        sys.stderr.write(f"[restore] {name} ← last-good (fallback)\n")

    _write_marker(restored)
    if restored:
        sys.stderr.write(f"[restore] подставлено из last-good: {', '.join(restored)}\n")
    else:
        sys.stderr.write("[restore] все свежие core-файлы валидны — fallback не нужен\n")
    return 0


def _write_marker(restored: list) -> None:
    from datetime import datetime, timezone
    with open(os.path.join(SITE, "_fallback.json"), "w", encoding="utf-8") as f:
        json.dump({"restored": restored, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                  f, ensure_ascii=False)


if __name__ == "__main__":
    raise SystemExit(main())
