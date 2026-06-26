#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Месячная история MOEX → model_output/momentum.json (скаляры) + model_output/returns.json (ряд).

Из месячных свечей (~7.6 года, interval=31) считаем:
  • momentum.json: WML 12-1 (Jegadeesh-Titman, как в нб 01) + годовая волатильность — для скоринга
    и inverse-vol взвешивания корзины (ребаланс momentum месячный → пересчитываем раз в месяц);
  • returns.json: ряд МЕСЯЧНЫХ доходностей по тикерам, выровненный на общую ось месяцев — фронт
    собирает из него ряд доходностей корзины и считает КОРРЕКТНЫЕ риск-метрики (Sharpe/Sortino/
    Calmar/maxDD/волатильность с учётом корреляций) + основа для ковариации (Фаза 3, оптимизатор).

CIRCUIT-BREAKER против троттлинга MOEX с облачных IP (как smartlab_reconcile): CB_MAX сетевых
ошибок ПОДРЯД → стоп, union с прошлым (ничего не теряем). Запускать ЛУЧШЕ ЛОКАЛЬНО + коммитить.

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
from moex_iss import fetch_candles, fetch_dividends  # noqa: E402

ARTIFACT = os.path.join(REPO, "model_output", "forecast_rf.json")
OUT_MOM = os.path.join(REPO, "model_output", "momentum.json")
OUT_RET = os.path.join(REPO, "model_output", "returns.json")
CB_MAX = 8                 # сетевых ошибок подряд → circuit-breaker
PAUSE = 0.15
HISTORY_DAYS = 2780        # ~7.6 года месячных свечей
MAX_MONTHS = 96            # ось ряда доходностей (8 лет)
VOL_WINDOW = 60            # окно для скалярной vol_ann (5 лет)


def main() -> int:
    art = json.load(open(ARTIFACT, encoding="utf-8"))
    tickers = [r["ticker"] for r in art["tickers"]]
    prev_mom = {}
    if os.path.exists(OUT_MOM):
        try:
            prev_mom = json.load(open(OUT_MOM, encoding="utf-8")).get("data", {})
        except Exception:  # noqa: BLE001
            prev_mom = {}
    mom_out = dict(prev_mom)
    series: dict = {}          # tk → {month 'YYYY-MM': ценовая доходность}
    div_series: dict = {}      # tk → {month: реализованная дивдоходность (дивиденд_месяца / цена_пред_месяца)}

    consec_err = n_ok = n_skip = n_err = 0
    tripped = False
    for tk in tickers:
        try:
            candles = fetch_candles(tk, days=HISTORY_DAYS, interval=31)   # месячные
            consec_err = 0
        except Exception as e:  # noqa: BLE001
            consec_err += 1
            n_err += 1
            sys.stderr.write(f"[momentum] {tk}: {e}\n")
            if consec_err >= CB_MAX:
                sys.stderr.write(f"[momentum] ⚡ CIRCUIT-BREAKER: {CB_MAX} ошибок подряд → стоп "
                                 f"(сохраняю {n_ok} свежих + прошлые)\n")
                tripped = True
                break
            time.sleep(1.0)
            continue
        if len(candles) < 13:
            n_skip += 1
            continue
        months = [d[:7] for d, _, _ in candles]
        closes = [c for _, c, _ in candles]
        values = [v for _, _, v in candles]      # месячный оборот, ₽
        # WML 12-1: последний ПОЛНЫЙ месяц [-2] к 12 мес. назад [-13] (скип текущего [-1])
        mom = (closes[-2] / closes[-13] - 1) if closes[-13] > 0 else None
        # месячные доходности (ключ = месяц закрытия)
        rets = {}
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                rets[months[i]] = closes[i] / closes[i - 1] - 1
        rvals = list(rets.values())
        vol = (statistics.pstdev(rvals[-VOL_WINDOW:]) * math.sqrt(12)) if len(rvals) >= 12 else None
        recent_val = [v for v in values[-VOL_WINDOW:] if v > 0]
        adv = round(statistics.mean(recent_val) / 21) if recent_val else None   # ADV: среднедневной оборот ₽ (≈21 торг.дней/мес)
        mom_out[tk] = {"mom": round(mom, 4) if mom is not None else None,
                       "vol_ann": round(vol, 4) if vol is not None else None, "adv": adv}
        series[tk] = rets
        # реальные дивиденды → месячная реализованная дивдоходность (дивиденд_месяца / цена пред. месяца)
        try:
            divs = fetch_dividends(tk)
        except Exception:  # noqa: BLE001
            divs = []
        div_by_month = {}
        for dt, val in divs:
            div_by_month[dt[:7]] = div_by_month.get(dt[:7], 0.0) + val
        close_by_month = dict(zip(months, closes))
        dy, prev_m = {}, None
        for mth in months:
            if prev_m and div_by_month.get(mth, 0) > 0 and close_by_month.get(prev_m, 0) > 0:
                dy[mth] = div_by_month[mth] / close_by_month[prev_m]
            prev_m = mth
        div_series[tk] = dy
        n_ok += 1
        time.sleep(PAUSE)

    # ── ось месяцев (последние MAX_MONTHS) + выравнивание рядов ──
    all_months = sorted({m for s in series.values() for m in s})
    axis = all_months[-MAX_MONTHS:]
    ret_data = {tk: [round(s[m], 4) if m in s else None for m in axis] for tk, s in series.items()}
    div_data = {tk: [round(s.get(m, 0.0), 5) for m in axis] for tk, s in div_series.items() if any(s.values())}

    asof = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    json.dump({"meta": {"asof": asof, "n": len(mom_out), "n_fresh": n_ok, "n_err": n_err,
                        "tripped": tripped, "source": "MOEX ISS monthly candles (TQBR), WML 12-1 + vol_ann"},
               "data": mom_out}, open(OUT_MOM, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"meta": {"asof": asof, "months": axis, "n_tickers": len(ret_data),
                        "note": "месячные ценовые доходности + реальная дивдоходность (блок div)"},
               "data": ret_data, "div": div_data}, open(OUT_RET, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[momentum] momentum.json: {len(mom_out)} | returns.json: {len(ret_data)} тикеров × "
          f"{len(axis)} мес ({axis[0] if axis else '—'}…{axis[-1] if axis else '—'}) | "
          f"свежих {n_ok} | ошибок {n_err} | breaker={'ДА' if tripped else 'нет'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
