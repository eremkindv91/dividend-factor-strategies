#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Прогноз капитала и прибыли банка для Residual Income.

Две вещи, которые здесь принципиальны.

1. **ROE не капитализируется навечно.** Спека §7.1 и здравый смысл: последний ROE
   содержит фазу цикла, разовые эффекты и эффект базы. Прогнозный ROE сходится к
   устойчивому terminal по экспоненциальному fade:

       ROE_t = ROE_term + (ROE_1 − ROE_term) × e^(−λ(t−1))

   Метод один на все банки и вынесен в конфиг — чтобы нельзя было «подобрать»
   траекторию под желаемую цену.

2. **Капитал катится по clean surplus.** BV_t = BV_(t−1) + NI_t − Div_t + прочее.
   Спека §6.3 запрещает молча считать «прочее» нулём, если оно существенно, поэтому
   модуль отдельно возвращает reconciliation по истории: если сумма
   (прибыль − дивиденды) не сходится с фактическим приростом капитала, это
   означает, что у эмитента значимы OCI/эмиссии/выкупы, и качество оценки падает.

ТОЛЬКО stdlib (см. residual_income.py).
"""
from __future__ import annotations

import math
from statistics import median

from residual_income import ForecastYear, residual_income


def normalized_roe(history: list[tuple[int, float]], min_years: int = 3) -> tuple[float | None, dict]:
    """Нормализованный ROE = медиана истории.

    Медиана, а не среднее: у банков в выборке есть годы с разовыми эффектами
    (переоценки, роспуск резервов), и одно такое наблюдение сдвигает среднее
    настолько, что прогноз перестаёт быть про бизнес.
    """
    vals = [v for _, v in history if v is not None and math.isfinite(v)]
    diag = {"observations": len(vals), "min_required": min_years}
    if len(vals) < min_years:
        diag["reason"] = "not_enough_history"
        return None, diag
    med = float(median(vals))
    diag.update({
        "median": round(med, 6),
        "last": round(float(vals[-1]), 6),
        "min": round(min(vals), 6),
        "max": round(max(vals), 6),
    })
    return med, diag


def roe_path(roe_start: float, roe_terminal: float, years: int, lam: float) -> list[float]:
    """Экспоненциальный fade от стартового ROE к устойчивому."""
    if years <= 0:
        return []
    out = []
    for t in range(1, years + 1):
        out.append(roe_terminal + (roe_start - roe_terminal) * math.exp(-lam * (t - 1)))
    return out


def clean_surplus_check(equity: list[tuple[int, float]],
                        net_income: dict[int, float],
                        dividends: dict[int, float],
                        tolerance: float = 0.10) -> dict:
    """Сходится ли история капитала по clean surplus.

    Считаем ожидаемый прирост (прибыль − дивиденды) и сравниваем с фактическим.
    Расхождение = всё, что не прошло через отчёт о прибылях: OCI, эмиссии, выкупы,
    изменение неконтролирующих долей. Мы это не выдумываем и не «чиним» — мы это
    измеряем и снижаем качество оценки, если разрыв велик.
    """
    pairs = sorted((y, v) for y, v in equity if v is not None and math.isfinite(v))
    checked, breaches, gaps = 0, 0, []
    for (y0, bv0), (y1, bv1) in zip(pairs, pairs[1:]):
        if y1 != y0 + 1:
            continue
        ni, div = net_income.get(y1), dividends.get(y1, 0.0)
        if ni is None or not math.isfinite(ni) or bv0 == 0:
            continue
        expected = bv0 + ni - (div or 0.0)
        gap = (bv1 - expected) / abs(bv0)
        checked += 1
        gaps.append(gap)
        if abs(gap) > tolerance:
            breaches += 1
    if not checked:
        return {"checked_years": 0, "status": "unknown",
                "note": "истории не хватает, чтобы проверить clean surplus"}
    worst = max(gaps, key=abs)
    return {
        "checked_years": checked,
        "breaches": breaches,
        "median_gap": round(float(median(gaps)), 4),
        "worst_gap": round(float(worst), 4),
        "tolerance": tolerance,
        "status": "ok" if breaches == 0 else ("noisy" if breaches <= checked // 2 else "broken"),
        "note": ("капитал катится по clean surplus" if breaches == 0 else
                 "прирост капитала не объясняется прибылью и дивидендами — значимы OCI, "
                 "эмиссии или выкупы; в прогнозе они НЕ моделируются"),
    }


def build_forecast(opening_equity: float,
                   roe_start: float,
                   roe_terminal: float,
                   payout: float,
                   cost_of_equity: float,
                   years: int,
                   fade_lambda: float,
                   first_year: int) -> list[ForecastYear]:
    """Прогнозная траектория: ROE → прибыль → дивиденды → капитал → RI → PV.

    Прочие изменения капитала принимаются нулевыми ЯВНО: моделировать OCI и
    эмиссии не из чего, а подставить туда оценку значило бы выдумать данные.
    Факт этого допущения уходит в clean_surplus_check и в качество.
    """
    path = roe_path(roe_start, roe_terminal, years, fade_lambda)
    rows: list[ForecastYear] = []
    bv = float(opening_equity)
    cum_df = 1.0
    for i, roe in enumerate(path):
        ni = roe * bv
        div = payout * ni if ni > 0 else 0.0        # из убытка дивиденд не платят
        other = 0.0
        closing = bv + ni - div + other
        ri = residual_income(ni, bv, cost_of_equity)
        cum_df /= (1.0 + cost_of_equity)
        rows.append(ForecastYear(
            year=first_year + i,
            opening_equity=bv,
            roe=roe,
            net_income=ni,
            payout=payout if ni > 0 else 0.0,
            dividends=div,
            other_equity_change=other,
            closing_equity=closing,
            cost_of_equity=cost_of_equity,
            residual_income=ri,
            discount_factor=cum_df,
            present_value=ri * cum_df,
        ))
        bv = closing
    return rows


def sustainable_growth(roe_terminal: float, payout: float) -> float:
    """g = ROE × (1 − payout).

    Рост капитала банка обеспечен удержанной прибылью — брать g «с потолка»
    нельзя, иначе терминал перестаёт быть связан с остальной моделью.
    """
    return float(roe_terminal) * (1.0 - float(payout))
