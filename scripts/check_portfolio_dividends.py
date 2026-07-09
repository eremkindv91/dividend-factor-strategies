#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Портфель-hotfix: аудит дивидендов спорных бумаг (T / TATN / TATNP / TRNFP / SNGSP).

Проверяет:
  • dividend flow считается как shares × dividend_per_share (не × lot_size) — это на фронте;
  • нет завышенных дивидендов (dps/price > 35% → почти наверняка до-сплитный/битый, напр. T 1:10);
  • T (Т-Технологии) не путается с TATN/TATNP (Татнефть) по названию;
  • ordinary/preferred (TATN vs TATNP) — раздельные записи.

Выход 0 (диагностика). CLI: python scripts/check_portfolio_dividends.py
"""
from __future__ import annotations

import json
import os
import sys

SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site")


def main() -> int:
    p = os.path.join(SITE, "data.json")
    if not os.path.exists(p):
        print("data.json отсутствует — пропуск"); return 0
    tk = {t["ticker"]: t for t in json.load(open(p, encoding="utf-8")).get("tickers", [])}
    notes = []

    for t in ("T", "TATN", "TATNP", "TRNFP", "SNGSP"):
        x = tk.get(t)
        if not x:
            print(f"  {t:6} нет в data.json"); continue
        price = x.get("price"); dps = x.get("dividend_forecast")
        yld = (dps / price) if (isinstance(dps, (int, float)) and isinstance(price, (int, float)) and price) else None
        flag = " ⚠ ЗАВЫШЕН (>35% — до-сплита/битый, исключается из дивпотока)" if (yld and yld > 0.35) else ""
        print(f"  {t:6} name={x.get('name')!r} dps={dps} price={price} implied_yield={yld and round(yld*100,1)}%{flag}")

    # T ≠ Татнефть
    tname = (tk.get("T") or {}).get("name", "")
    if "ТАТН" in tname.upper() or "ТАТНЕФ" in tname.upper():
        notes.append("❌ T мапится на Татнефть по названию!")
    else:
        notes.append("✓ T не путается с Татнефтью (name: %r)" % tname)
    # TATN / TATNP раздельны
    if "TATN" in tk and "TATNP" in tk and tk["TATN"].get("name") != tk["TATNP"].get("name"):
        notes.append("✓ TATN и TATNP — раздельные бумаги")
    for n in notes:
        print(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
