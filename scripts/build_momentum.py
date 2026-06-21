#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WML 12-1 momentum + годовая волатильность по дневным свечам MOEX → model_output/momentum.json.

Momentum (Jegadeesh-Titman, как в нб 01_factor_backtest): 11-мес доходность со СКИПОМ последнего
месяца — единственный фактор, устойчиво работающий на РФ на долгом сроке; ребаланс МЕСЯЧНЫЙ, поэтому
пересчитываем раз в месяц. vol_ann (годовая волатильность дневных доходностей) — для inverse-vol
взвешивания и будущей ковариации (Фаза 3).

CIRCUIT-BREAKER против троттлинга MOEX с облачных IP (как в smartlab_reconcile): CB_MAX сетевых
ошибок ПОДРЯД → стоп, сохраняем что успели + прошлый momentum.json (union, ничего не теряем).
Запускать ЛУЧШЕ ЛОКАЛЬНО (чистый IP) и коммитить momentum.json; CI — fallback с брейкером.

Запуск:  python scripts/build_momentum.py
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
from moex_iss import fetch_candles  # noqa: E402

ARTIFACT = os.path.join(REPO, "model_output", "forecast_rf.json")
OUT = os.path.join(REPO, "model_output", "momentum.json")
CB_MAX = 8               # сетевых ошибок подряд → circuit-breaker
PAUSE = 0.15             # вежливая пауза между тикерами
MIN_CANDLES = 30


def monthly_closes(candles):
    """Последнее закрытие каждого месяца (свечи по возрастанию)."""
    bym = {}
    for d, c in candles:
        bym[d[:7]] = c                       # перезапись → остаётся последняя сделка месяца
    return [bym[m] for m in sorted(bym)]


def momentum_12_1(monthly):
    """11-мес доходность со скипом текущего месяца: M[-2]/M[-13]−1. Нужно ≥13 мес."""
    if len(monthly) < 13:
        return None
    m2, m13 = monthly[-2], monthly[-13]
    return (m2 / m13 - 1) if m13 > 0 else None


def vol_annualized(candles):
    """Годовая волатильность дневных доходностей (последние ~252 дня)."""
    closes = [c for _, c in candles][-253:]
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 20:
        return None
    return statistics.pstdev(rets) * math.sqrt(252)


def main() -> int:
    art = json.load(open(ARTIFACT, encoding="utf-8"))
    tickers = [r["ticker"] for r in art["tickers"]]
    prev = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT, encoding="utf-8")).get("data", {})
        except Exception:  # noqa: BLE001
            prev = {}
    out = dict(prev)                          # union: непролитые тикеры сохраняют прошлые значения

    consec_err = n_ok = n_skip = n_err = 0
    tripped = False
    for tk in tickers:
        try:
            candles = fetch_candles(tk)
            consec_err = 0
        except Exception as e:                # noqa: BLE001 — сетевая ошибка/троттлинг
            consec_err += 1
            n_err += 1
            sys.stderr.write(f"[momentum] {tk}: {e}\n")
            if consec_err >= CB_MAX:
                sys.stderr.write(f"[momentum] ⚡ CIRCUIT-BREAKER: {CB_MAX} ошибок подряд → стоп "
                                 f"(сохраняю {n_ok} свежих + прошлые для остальных)\n")
                tripped = True
                break
            time.sleep(1.0)
            continue
        if len(candles) < MIN_CANDLES:
            n_skip += 1
            continue
        mom = momentum_12_1(monthly_closes(candles))
        vol = vol_annualized(candles)
        if mom is None and vol is None:
            n_skip += 1
            continue
        out[tk] = {"mom": round(mom, 4) if mom is not None else None,
                   "vol_ann": round(vol, 4) if vol is not None else None}
        n_ok += 1
        time.sleep(PAUSE)

    data = {"meta": {"asof": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d"),
                     "n": len(out), "n_fresh": n_ok, "n_err": n_err, "tripped": tripped,
                     "source": "MOEX ISS daily candles (TQBR), WML 12-1 + vol_ann"},
            "data": out}
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[momentum] записано {os.path.relpath(OUT, REPO)}: всего {len(out)} | свежих {n_ok} | "
          f"пропущено {n_skip} | ошибок {n_err} | breaker={'ДА' if tripped else 'нет'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
