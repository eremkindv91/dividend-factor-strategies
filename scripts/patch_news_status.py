#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Патч news-блока в site_status.json после освежения news.json (вызывается из news.yml).

site_status.json строится в update.yml (09:00/19:00), а news.json обновляется чаще (news.yml
07:00/09:20/19:00). Без патча свежие новости какое-то время показывались бы как устаревшие.
Здесь пересчитываем ТОЛЬКО news-блок по фактическому news.json и overall — тем же
классификатором, что и build_site_status (новости оцениваются по СВОЕЙ частоте, не по
торговому календарю MOEX). Чистый stdlib. Идемпотентно.

CLI: python scripts/patch_news_status.py <dir-с-gh-pages>  (там лежат news.json и site_status.json)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_site_status as bss  # noqa: E402

NEWS_CFG = ("news", "Новости", "news.json", ["generated_at", "date"], "news", None)


def main() -> int:
    d = sys.argv[1] if len(sys.argv) > 1 else "."
    ns_path, st_path = os.path.join(d, "news.json"), os.path.join(d, "site_status.json")
    if not (os.path.exists(ns_path) and os.path.exists(st_path)):
        sys.stderr.write("[patch-news] нет news.json или site_status.json — пропуск\n")
        return 0
    try:
        news = json.load(open(ns_path, encoding="utf-8"))
        st = json.load(open(st_path, encoding="utf-8"))
    except (ValueError, OSError) as e:
        sys.stderr.write(f"[patch-news] битый JSON — пропуск ({e})\n")
        return 0

    now = datetime.now(timezone.utc)
    fb = st.get("blocks", {}).get("news", {}).get("fallback_status") == "fallback"
    st.setdefault("blocks", {})["news"] = bss.classify_block(NEWS_CFG, news, fb, now)

    # пересчёт overall по всем блокам в новой модели
    worst = "fresh"
    for b in st["blocks"].values():
        f = b.get("freshness_status") or b.get("status", "fresh")
        if bss.SEVERITY.get(f, 0) > bss.SEVERITY.get(worst, 0):
            worst = f
    st["overall"] = bss.COARSE.get(worst, "stale")
    st["overall_status"] = worst
    st["overall_message"] = bss.overall_message(worst)

    with open(st_path, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    sys.stderr.write(f"[patch-news] news→{st['blocks']['news']['freshness_status']}, overall→{worst}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
