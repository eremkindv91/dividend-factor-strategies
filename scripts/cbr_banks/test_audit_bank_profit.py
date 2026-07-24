#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression-тесты месячной прибыли Ф.102 (сдвиг периода, привязка period_month, свежесть).
Запуск: python scripts/cbr_banks/test_audit_bank_profit.py   (или pytest).
Совместимо с pytest и с прямым запуском (свой раннер снизу)."""
from __future__ import annotations

import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # для scripts/audit_bank_profit

from build_cbr_banks import (add_quarter_deltas, compute_is_stale,  # noqa: E402
                             prev_month, validate_timeseries)


def _mk(date_str, value):
    return {"date": date_str, "value": value, "symbol": "61101", "form": "102", "unit": "тыс. руб."}


# 1) накопительные: май=100 → июнь=115 ⇒ месячный июнь=15
def test_cumulative_delta_basic():
    pts = [_mk("2026-06-01", 100.0), _mk("2026-07-01", 115.0)]   # отчёты на 01.06 (=май), 01.07 (=июнь)
    add_quarter_deltas(pts)
    jun = next(p for p in pts if p["date"] == "2026-07-01")
    assert jun["value_q"] == 15.0
    assert jun["period_month"] == "2026-06"     # дельта относится к ИЮНЮ


# 2) январь НЕ вычитается из декабря прошлого года
def test_january_not_subtracted_from_december():
    # 2026-01-01 = весь 2025 (накопл. год 2025); 2026-02-01 = январь 2026 (первый месяц накопл.года 2026)
    pts = [_mk("2025-12-01", 30_000.0), _mk("2026-01-01", 44_000.0), _mk("2026-02-01", 2_600.0)]
    add_quarter_deltas(pts)
    jan = next(p for p in pts if p["date"] == "2026-02-01")
    assert jan["value_q"] == 2_600.0            # не 2600−44000; первый месяц года = сам накопл.
    assert jan["period_month"] == "2026-01"     # относится к январю 2026
    dec = next(p for p in pts if p["date"] == "2026-01-01")
    assert dec["value_q"] == 14_000.0           # 44000−30000 = декабрь 2025
    assert dec["period_month"] == "2025-12"


# 3) последняя строка периода НЕ теряется
def test_last_row_not_lost():
    pts = [_mk("2026-04-01", 10.0), _mk("2026-05-01", 30.0), _mk("2026-06-01", 45.0)]
    add_quarter_deltas(pts)
    assert pts[-1]["date"] == "2026-06-01"
    assert "value_q" in pts[-1] and "period_month" in pts[-1]


# 4) labels и values одинаковой длины (каждая точка = метка+значение)
def test_labels_values_same_length():
    pts = [_mk("2026-04-01", 10.0), _mk("2026-05-01", 30.0)]
    add_quarter_deltas(pts)
    labels = [p["period_month"] for p in pts]
    values = [p["value_q"] for p in pts]
    assert len(labels) == len(values) == len(pts)


# 5) значение связано с датой через КЛЮЧ, а не позицию массива
def test_value_tied_to_date_by_key():
    pts = [_mk("2026-05-01", 30.0), _mk("2026-04-01", 10.0)]   # намеренно не отсортировано
    add_quarter_deltas(pts)
    by_date = {p["date"]: p for p in pts}
    assert by_date["2026-05-01"]["value_q"] == 20.0            # 30−10, независимо от исходного порядка
    assert by_date["2026-05-01"]["period_month"] == "2026-04"


# 6) при дублировании отчётной даты валидация детерминированно это ловит (не молчаливый first())
def test_duplicate_date_detected():
    ts = {"963": {"net_profit": [_mk("2026-06-01", 100.0), _mk("2026-06-01", 999.0)]}}
    for p in ts["963"]["net_profit"]:
        p["period_month"] = prev_month(p["date"])
    metrics = [{"metric_id": "net_profit", "cumulative": True, "unit": "тыс. руб."}]
    status, issues = validate_timeseries(ts, metrics)
    assert status == "warning"
    assert any("дубли" in i for i in issues)


# 7) реальные значения Совкомбанка попадают в ВЕРНЫЕ месяцы
def test_sovcombank_values_in_correct_months():
    # накопл. 61101 из ЦБ (тыс. руб.) — с начала года накопления 2026, чтобы дельты были реальными
    cum = [("2026-02-01", 2_604_425), ("2026-03-01", 6_010_298), ("2026-04-01", 15_897_710),
           ("2026-05-01", 39_310_297), ("2026-06-01", 49_535_977), ("2026-07-01", 64_220_236)]
    pts = [_mk(d, v) for d, v in cum]
    add_quarter_deltas(pts)
    by_period = {p["period_month"]: round(p["value_q"] / 1e6, 1) for p in pts}
    assert by_period["2026-03"] == 9.9      # март
    assert by_period["2026-04"] == 23.4     # апрель
    assert by_period["2026-05"] == 10.2     # май
    assert by_period["2026-06"] == 14.7     # ИЮНЬ = 14.7 (не 10.2!)


# 8) отсутствие свежего периода НЕ подставляет прошлое значение под новой датой
def test_no_fabrication_of_missing_period():
    # доступно только до 2026-06-01 → нет точки 2026-07-01, значение НЕ дублируется под июнь
    pts = [_mk("2026-05-01", 39_310_297), _mk("2026-06-01", 49_535_977)]
    add_quarter_deltas(pts)
    periods = {p["period_month"] for p in pts}
    assert "2026-06" not in periods          # июня (истинного) в наборе нет — не выдуман


# 9) устаревание отмечается явно (is_stale)
def test_stale_flag_explicit():
    # 24 июля, последний отчёт 01.06 → устарел (ждём 01.07); после подтягивания 01.07 — свежо
    assert compute_is_stale("2026-06-01", date(2026, 7, 24)) is True
    assert compute_is_stale("2026-07-01", date(2026, 7, 24)) is False
    assert compute_is_stale("2026-06-01", date(2026, 7, 10)) is False   # до окна публикации ЦБ
    assert compute_is_stale(None, date(2026, 7, 24)) is True


# 10) единицы/суммы преобразуются РОВНО один раз (value_q в тех же тыс. руб., без /1000 и ×1000)
def test_units_converted_once():
    pts = [_mk("2026-05-01", 39_310_297), _mk("2026-06-01", 49_535_977)]
    add_quarter_deltas(pts)
    jun = next(p for p in pts if p["date"] == "2026-06-01")
    assert jun["value_q"] == 10_225_680.0    # тыс. руб., та же единица что и value (без доп. деления)
    assert jun["unit"] == "тыс. руб."


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ok = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            ok += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{ok}/{len(tests)} тестов пройдено")
    return 0 if ok == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_run())
