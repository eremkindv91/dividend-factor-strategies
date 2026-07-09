#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Портфель-hotfix: детект split-like аномалий в месячных рядах returns.json.

Total return = ценовой (data) + дивидендный (div). Split/corporate action без очистки даёт
разовый скачок в разы (напр. TRNFP: дивряд 11.74 = 1174% → фейковые 600% годовой волатильности
и 80–90% вклада в риск портфеля). Фронт (pfxSeriesAnomaly, порог |move|>2.5) исключает такие
бумаги из VaR/CVaR. Этот скрипт — офлайн-аудит того же: печатает список аномальных тикеров.

Выход 0 (диагностика). CLI: python scripts/check_price_series_anomalies.py
"""
from __future__ import annotations

import json
import os
import sys

SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site")
THRESHOLD = 2.5   # |месячный total-return| > 250% → split-like


def main() -> int:
    p = os.path.join(SITE, "returns.json")
    if not os.path.exists(p):
        print("returns.json отсутствует — пропуск"); return 0
    ret = json.load(open(p, encoding="utf-8"))
    data, div = ret.get("data") or {}, ret.get("div") or {}
    anomalies = []
    for tk, pr in data.items():
        dv = div.get(tk) or []
        worst = 0.0
        for i, r in enumerate(pr):
            tot = (r if isinstance(r, (int, float)) else 0) + (dv[i] if i < len(dv) and isinstance(dv[i], (int, float)) else 0)
            if abs(tot) > abs(worst):
                worst = tot
        if abs(worst) > THRESHOLD:
            anomalies.append((tk, worst))
    anomalies.sort(key=lambda x: -abs(x[1]))
    if anomalies:
        print(f"Найдено {len(anomalies)} рядов со split-like разрывом (|move| > {THRESHOLD*100:.0f}%):")
        for tk, w in anomalies[:20]:
            print(f"  {tk:8} max месячный total-return = {w:+.2f} ({w*100:+.0f}%) → исключается из риск-метрик")
    else:
        print("Split-like аномалий не найдено.")
    # sanity: явно проверяем известную проблему
    if any(tk == "TRNFP" for tk, _ in anomalies):
        print("  ✓ TRNFP помечен как аномальный (ожидаемо: сплит Транснефти).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
