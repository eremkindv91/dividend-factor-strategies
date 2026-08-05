#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Чей срез облигаций свежее: опубликованный на gh-pages или из чекаута репозитория.

Зачем: скринер генерит bonds_update.yml и кладёт результат прямо в gh-pages, а update.yml
делает orphan-republish и копирует ЗАКОММИЧЕННУЮ копию. Бот коммитит не при каждом успешном
прогоне, поэтому копия в репозитории отстаёт — и деплой откатывал облигации на более старые
цены. После этого safe-фильтр честно отсеивал весь универсум как устаревший, и раздел
выглядел пустым, хотя свежие данные на сайте уже были.

Выход: код 0 — опубликованный свежее (его и надо оставить), 1 — брать копию из чекаута.
Ошибка чтения любого файла трактуется как «он не свежее»: молчаливая подмена рабочих
данных битыми хуже, чем лишний день старых цен.

    python scripts/pick_fresher_bonds.py <published.json> <checkout.json>
"""
from __future__ import annotations

import json
import sys


def newest_price_date(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            rows = json.load(handle).get("bonds") or []
    except (OSError, ValueError, AttributeError):
        return ""
    dates = [(row.get("source_dates") or {}).get("price") or "" for row in rows]
    return max(dates) if dates else ""


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write("нужны два пути: published и checkout\n")
        return 1
    published, checkout = newest_price_date(argv[1]), newest_price_date(argv[2])
    sys.stderr.write(f"[bonds] опубликовано: {published or 'н/д'} · чекаут: {checkout or 'н/д'}\n")
    return 0 if published and published > checkout else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
