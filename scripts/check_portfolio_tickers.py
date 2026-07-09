#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Портфель-hotfix: аудит покрытия тикеров и справочника.

Проверяет наличие обязательных бумаг в data.json (autocomplete/риск-универсум) и документирует
пробелы. SNGS/SNGSP отсутствуют в пайплайне проекта — на фронте они добавлены в autocomplete
(PFX_EXTRA_TICKERS) и обрабатываются с честным warning (исключены из VaR/CVaR). Алиасы и правило
«T≠TATN, SNGSP≠SNGS» живут во фронте (PFX_ALIASES в app.js) — здесь проверяем только данные.

Выход 0 (диагностика). CLI: python scripts/check_portfolio_tickers.py
"""
from __future__ import annotations

import json
import os
import sys

SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site")
REQUIRED = ["SBER", "SBERP", "SNGS", "SNGSP", "TATN", "TATNP", "TRNFP", "T",
            "MOEX", "LKOH", "GAZP", "PLZL", "CHMF", "NVTK", "GMKN", "ROSN", "FEES"]


def main() -> int:
    p = os.path.join(SITE, "data.json")
    if not os.path.exists(p):
        print("data.json отсутствует — пропуск"); return 0
    have = {t["ticker"] for t in json.load(open(p, encoding="utf-8")).get("tickers", [])}
    present = [t for t in REQUIRED if t in have]
    missing = [t for t in REQUIRED if t not in have]
    print(f"В data.json есть {len(present)}/{len(REQUIRED)} обязательных: {', '.join(present)}")
    if missing:
        print(f"Отсутствуют в data.json (autocomplete добавляет из PFX_EXTRA_TICKERS, риск исключён): {', '.join(missing)}")
    # ключевые инварианты справочника (задокументированы, реализованы во фронте app.js)
    print("Справочник (app.js PFX_ALIASES / pfxCanonTicker): TCSG→T, СУРГУТ АП→SNGSP, ТАТНЕФТЬ АП→TATNP;")
    print("  инварианты: T≠TATN, SNGSP≠SNGS, TATN≠TATNP — резолв только по точным ключам, без кросс-маппинга.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
