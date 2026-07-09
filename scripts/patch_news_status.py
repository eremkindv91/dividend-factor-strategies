#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Патч news-блока в site_status.json после освежения news.json (вызывается из news.yml).

site_status.json строится в update.yml (09:00/19:00), а news.json обновляется чаще (news.yml
07:00/09:20/19:00). Без патча свежие новости какое-то время показывались бы как stale. Здесь
пересчитываем ТОЛЬКО news-блок по фактическому news.json (и overall) прямо в опубликованном
site_status.json, не трогая остальные блоки. Чистый stdlib. Идемпотентно.

CLI: python scripts/patch_news_status.py <dir-с-gh-pages>  (там лежат news.json и site_status.json)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

NEWS_THRESH_DAYS = 0.6


def age_days(s):
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 86400
    except (ValueError, TypeError):
        return None


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
    asof = news.get("generated_at") or news.get("date")
    age = age_days(asof)
    status = "broken" if not news.get("market_snapshot") else \
        ("fresh" if (age is not None and age <= NEWS_THRESH_DAYS) else "stale")
    blocks = st.setdefault("blocks", {})
    blocks["news"] = {"title": "Новости", "status": status, "asof": str(asof)[:19] if asof else None,
                      "age_days": round(age, 1) if age is not None else None, "note": ""}
    order = {"fresh": 0, "stale": 1, "fallback": 2, "broken": 3}
    st["overall"] = max((b.get("status", "fresh") for b in blocks.values()), key=lambda s: order.get(s, 0))
    with open(st_path, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    sys.stderr.write(f"[patch-news] news→{status} (age {age and round(age,2)}), overall→{st['overall']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
