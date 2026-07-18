#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Единый конфигурационный слой двухконтурной «Пилы» рынка.

MCFTR (полная доходность) — основной инвестиционный режим; IMOEX (ценовой индекс) —
дополнительный ценовой swing/zigzag-контур. Параметры контуров НЕ смешиваются: уровни
IMOEX никогда не применяются к MCFTR и наоборот. Изменение любого порога — L3-решение.
"""
from __future__ import annotations

# board fallback: (engine, market, board). ISS discover_index_board уточняет primary-доску.
INDEX_CONFIGS = {
    "MCFTR": {
        "secid": "MCFTR",
        "mode": "total_return",
        "methodology": "investment_regime",
        "reversal_threshold": 0.095,
        "fallback_board": ("stock", "index", "RTSI"),   # MCFTR торгуется на RTSI, НЕ на SNDX
    },
    "IMOEX": {
        "secid": "IMOEX",
        "mode": "price_index",
        "methodology": "price_saw",
        "reversal_threshold": 0.07,
        "buy_drawdown": 0.13,          # зона покупки: −13% от последнего подтверждённого максимума
        "fix_rally": 0.17,             # зона фиксации: +17% от последнего подтверждённого минимума
        "fallback_board": ("stock", "index", "SNDX"),   # IMOEX primary-доска — SNDX
    },
}

DEFAULT_INDEX = "MCFTR"
SCHEMA = 2
MIN_POINTS = 100
STALE_FAIL_DAYS = 20               # ряд старше → пайплайн сломан (январские каникулы ~10 дн — норма)


def get_config(index: str) -> dict:
    if index not in INDEX_CONFIGS:
        raise KeyError(f"неизвестный индекс {index!r}; известны: {list(INDEX_CONFIGS)}")
    return INDEX_CONFIGS[index]
