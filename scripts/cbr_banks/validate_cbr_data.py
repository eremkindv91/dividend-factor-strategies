#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Валидация опубликованных данных ЦБ (site/cbr/*.json). Чистый stdlib. Служит и как тест, и как
предпубликационный гейт в update-cbr-banks.yml. Exit 1 при критических ошибках.

Гарантии (никакой синтетики / трассируемость):
  • каждое опубликованное значение — число (не NaN/null), у него есть form, symbol, source;
  • нет «дорисованных» значений: пропуски НЕ публикуются как числа;
  • banks/metrics/metadata/data_quality согласованы;
  • reliability только из разрешённого набора.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import date

CBR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "site", "cbr")
ERRORS: list[str] = []
ALLOWED_RELIABILITY = {"official_direct", "calculated_from_official", "calculated"}


def err(m): ERRORS.append(m)


def load(name):
    p = os.path.join(CBR, name)
    if not os.path.exists(p):
        err(f"{name}: файл отсутствует"); return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    banks = load("banks.json")
    ts = load("bank_timeseries.json")
    meta = load("metadata.json")
    dq = load("data_quality.json")
    mapping = load("metric_mapping.json")
    if None in (banks, ts, meta, dq, mapping):
        for e in ERRORS:
            print("  ERROR:", e)
        print("ВАЛИДАЦИЯ CBR ПРОВАЛЕНА (нет файлов)")
        return 1

    # banks.json
    if not isinstance(banks, list) or not banks:
        err("banks.json: пустой список")
    reg_set = set()
    for b in banks:
        for k in ("reg_num", "name", "source_url", "is_active", "is_systemically_important"):
            if k not in b:
                err(f"banks.json: у банка нет поля {k}")
        reg_set.add(str(b.get("reg_num")))

    # metric_mapping
    sym_by_metric = {}
    for m in mapping.get("metrics", []):
        if not m.get("symbol"):
            err(f"metric_mapping: у метрики {m.get('metric_id')} пустой symbol (неподтверждённый код)")
        if m.get("reliability_level") not in ALLOWED_RELIABILITY:
            err(f"metric_mapping: недопустимый reliability {m.get('reliability_level')} у {m.get('metric_id')}")
        sym_by_metric[m.get("metric_id")] = m.get("symbol")

    # bank_timeseries — трассируемость + отсутствие синтетики
    n_points = 0
    for reg, metrics in ts.items():
        for metric_id, points in metrics.items():
            for p in points:
                n_points += 1
                v = p.get("value")
                if not isinstance(v, (int, float)) or (isinstance(v, float) and math.isnan(v)):
                    err(f"ts {reg}/{metric_id} {p.get('date')}: значение не число/NaN ({v})")
                for k in ("date", "symbol", "form", "source"):
                    if not p.get(k):
                        err(f"ts {reg}/{metric_id}: точка без {k} (нет трассировки к источнику)")
                try:
                    date.fromisoformat(str(p.get("date")))
                except (ValueError, TypeError):
                    err(f"ts {reg}/{metric_id}: битая дата {p.get('date')}")
                # символ точки должен совпадать с маппингом метрики (нет подмены)
                if metric_id in sym_by_metric and str(p.get("symbol")) != str(sym_by_metric[metric_id]):
                    err(f"ts {reg}/{metric_id}: symbol {p.get('symbol')} ≠ маппингу {sym_by_metric[metric_id]}")
                # расчётная дельта «за период»: если есть — число + помечена методом
                if "value_q" in p:
                    vq = p["value_q"]
                    if not isinstance(vq, (int, float)) or (isinstance(vq, float) and math.isnan(vq)):
                        err(f"ts {reg}/{metric_id} {p.get('date')}: value_q не число ({vq})")
                    if p.get("value_q_method") != "calculated_from_official":
                        err(f"ts {reg}/{metric_id}: value_q без пометки calculated_from_official")
                # нормативы (%) — sanity-диапазон
                if p.get("unit") == "%" and isinstance(v, (int, float)) and not (0 <= v <= 10000):
                    err(f"ts {reg}/{metric_id} {p.get('date')}: норматив вне [0,10000]%: {v}")   # Н2/Н3 у сворачивающихся банков бывают в тысячи %

    if n_points == 0:
        err("bank_timeseries: ноль точек")

    # metadata
    for k in ("source", "source_url", "last_report_date", "forms", "generated_at"):
        if not meta.get(k):
            err(f"metadata: нет {k}")

    # data_quality согласованность
    if dq.get("banks_total") != len(banks):
        err(f"data_quality.banks_total={dq.get('banks_total')} ≠ banks {len(banks)}")

    print(f"  банков: {len(banks)} | точек: {n_points} | last_report_date: {meta.get('last_report_date')}")

    # valuation.json (секторная оценка) — необязателен, но если есть, проверяем контракт
    val = load("valuation.json")
    if val is not None:
        vm = val.get("meta") or {}
        if not isinstance(vm.get("disclaimer"), str) or not vm.get("disclaimer"):
            err("valuation.json: нет фиксированного дисклеймера")
        vb = val.get("banks") or []
        if not vb:
            err("valuation.json: banks пуст")
        for b in vb:
            if not isinstance(b.get("warnings"), list):
                err(f"valuation.json: {b.get('ticker')} — warnings не список (глушится слой качества)")
            for k, lo, hi in (("p_bv", 0.05, 5), ("roe", -50, 80), ("n10", 0, 100), ("payout", -10, 300)):
                v = b.get(k)
                if isinstance(v, (int, float)) and not (lo <= v <= hi):
                    err(f"valuation.json: {b.get('ticker')} {k}={v} вне разумного диапазона")
        print(f"  valuation.json: {vm.get('banks_valued')}/{vm.get('banks_count')} банков, "
              f"ЦБ {vm.get('cbr_asof')} · MOEX {vm.get('moex_asof')}")

    if ERRORS:
        for e in ERRORS:
            print("  ERROR:", e)
        print(f"ВАЛИДАЦИЯ CBR ПРОВАЛЕНА: {len(ERRORS)} ошибок")
        return 1
    print("ВАЛИДАЦИЯ CBR OK (все значения трассируются к ЦБ, синтетики нет)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
