# -*- coding: utf-8 -*-
"""Тесты временного контракта: source_as_of (дата данных) ≠ generated_at (дата прогона).
Фиксированное время. Покрывают 12 сценариев ТЗ §6."""
import json
import os
import sys
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_site_status as bss  # noqa: E402
import trading_calendar as tc  # noqa: E402
import moex_iss as mi  # noqa: E402


def msk(y, mo, d, h=12, mi_=0):
    return datetime(y, mo, d, h, mi_, tzinfo=tc.MSK)


def utc_of(y, mo, d, h=12, mi_=0):
    return msk(y, mo, d, h, mi_).astimezone(timezone.utc)


def _site(tmp, files):
    for name, obj in files.items():
        p = os.path.join(tmp, name)
        d = os.path.dirname(p)
        if d and d != tmp:
            os.makedirs(d, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f)
    return tmp


# ── источник цены (moex_iss): дата свечи, не дата прогона ──

def test_1_rebuilt_sunday_price_stays_friday():
    # SYSTIME=воскресенье → сегодняшнего close нет → asof = PREVDATE (пятница)
    assert mi._session_close_date("2026-07-12 22:00:00") is None
    assert mi._price_asof("LCLOSEPRICE", "2026-07-10", mi._session_close_date("2026-07-12 22:00:00")) == "2026-07-10"


def test_4_new_price_after_session_close():
    # торговый день после 19:00 → сегодняшний close существует
    assert mi._session_close_date("2026-07-10 19:30:00") == "2026-07-10"
    assert mi._price_asof("LCLOSEPRICE", "2026-07-09", "2026-07-10") == "2026-07-10"


def test_intraday_before_close_uses_prevdate():
    assert mi._session_close_date("2026-07-10 11:00:00") is None
    assert mi._price_asof("LAST", "2026-07-09", None) == "2026-07-09"


def test_holiday_systime_no_today_close():
    assert mi._session_close_date("2026-05-09 20:00:00") is None   # 9 мая — праздник


# ── cbr: дата котировки банка честная ──

def test_7_cbr_file_today_observation_yesterday():
    from scripts.cbr_banks import build_banks_valuation as bv
    # SYSTIME воскресенье → дата цены = PREVDATE (пятница), не сегодня
    assert bv._moex_price_date("2026-07-12 22:00:00", "2026-07-10") == "2026-07-10"
    # торговый день после закрытия → сегодня
    assert bv._moex_price_date("2026-07-10 19:30:00", "2026-07-09") == "2026-07-10"


# ── read-слой build_site_status: разделение полей ──

def test_2_rebuilt_multiple_times_source_as_of_stable(tmp_path):
    tmp = str(tmp_path)
    # два «прогона»: разный generated_at, та же дата цены → source_as_of не двигается
    _site(tmp, {"data.json": {"meta": {"price_asof": "2026-07-10", "обновлено": "2026-07-12T09:15:00"}}})
    p1 = bss.build_status(now_utc=utc_of(2026, 7, 12, 9, 20), site_dir=tmp)
    _site(tmp, {"data.json": {"meta": {"price_asof": "2026-07-10", "обновлено": "2026-07-12T19:15:00"}}})
    p2 = bss.build_status(now_utc=utc_of(2026, 7, 12, 19, 20), site_dir=tmp)
    assert p1["blocks"]["market"]["source_as_of"] == "2026-07-10"
    assert p2["blocks"]["market"]["source_as_of"] == "2026-07-10"
    assert p1["blocks"]["market"]["generated_at"] != p2["blocks"]["market"]["generated_at"]


def test_generated_at_not_used_as_source_as_of(tmp_path):
    tmp = str(tmp_path)
    _site(tmp, {"data.json": {"meta": {"price_asof": "2026-07-10", "обновлено": "2026-07-12T09:15:00"}}})
    b = bss.build_status(now_utc=utc_of(2026, 7, 12, 12, 0), site_dir=tmp)["blocks"]["market"]
    assert b["source_as_of"] == "2026-07-10"                 # дата данных
    assert b["generated_at"].startswith("2026-07-12")        # дата прогона — отдельно
    assert "10 июля" in b["user_message"]                    # UI показывает дату ДАННЫХ


def test_5_market_history_ends_earlier_than_generated_at(tmp_path):
    tmp = str(tmp_path)
    _site(tmp, {"data.json": {"meta": {"price_asof": "2026-07-10"}},
                "market_history.json": {"generated_at": "2026-07-12T19:56:48+00:00",
                                        "data_asof": "2026-07-10"}})
    b = bss.build_status(now_utc=utc_of(2026, 7, 12, 12, 0), site_dir=tmp)["blocks"]["market_history"]
    assert b["source_as_of"] == "2026-07-10"                 # max дата ряда, не generated_at
    assert b["generated_at"].startswith("2026-07-12")


def test_12_backward_compat_old_market_history_without_data_asof(tmp_path):
    tmp = str(tmp_path)
    # старый контракт: только generated_at → деградируем к нему (не падаем)
    _site(tmp, {"data.json": {"meta": {"price_asof": "2026-07-10"}},
                "market_history.json": {"generated_at": "2026-07-10T19:56:48+00:00"}})
    b = bss.build_status(now_utc=utc_of(2026, 7, 12, 12, 0), site_dir=tmp)["blocks"]["market_history"]
    assert b["source_as_of"] == "2026-07-10"                 # generated_at как fallback-дата


def test_6_news_today_market_friday(tmp_path):
    tmp = str(tmp_path)
    _site(tmp, {"data.json": {"meta": {"price_asof": "2026-07-10"}},
                "news.json": {"generated_at": "2026-07-12T09:00:00", "date": "2026-07-12",
                              "market_snapshot": {"x": 1}}})
    blks = bss.build_status(now_utc=utc_of(2026, 7, 12, 12, 0), site_dir=tmp)["blocks"]
    assert blks["market"]["source_as_of"] == "2026-07-10"    # рынок — пятница
    assert blks["news"]["source_as_of"] == "2026-07-12"      # новости — сегодня, независимо


def test_8_derived_marlamov_inherits_price_asof(tmp_path):
    tmp = str(tmp_path)
    # meta.updated = сегодня (пересчёт), meta.source_as_of = дата цен входа (пятница)
    _site(tmp, {"marlamov.json": {"meta": {"updated": "2026-07-12T20:00:00",
                                           "source_as_of": "2026-07-10"}}})
    b = bss.build_status(now_utc=utc_of(2026, 7, 12, 12, 0), site_dir=tmp)["blocks"]["marlamov"]
    assert b["source_as_of"] == "2026-07-10"                 # наследует старейший критичный вход
    assert b["generated_at"].startswith("2026-07-12")        # время пересчёта — отдельно


def test_9_missing_date_not_confirmed(tmp_path):
    tmp = str(tmp_path)
    _site(tmp, {"data.json": {"meta": {"обновлено": "2026-07-12T09:15:00"}}})   # нет price_asof
    b = bss.build_status(now_utc=utc_of(2026, 7, 12, 12, 0), site_dir=tmp)["blocks"]["market"]
    assert b["source_as_of"] is None                         # не подставляем текущую дату
    assert b["user_message"] == "Дата исходных данных не подтверждена"


def test_10_future_date_rejected(tmp_path):
    tmp = str(tmp_path)
    _site(tmp, {"data.json": {"meta": {"price_asof": "2026-07-20"}}})           # будущая дата
    b = bss.build_status(now_utc=utc_of(2026, 7, 12, 12, 0), site_dir=tmp)["blocks"]["market"]
    assert b["source_as_of"] is None
    assert b["user_message"] == "Дата исходных данных не подтверждена"


def test_11_utc_moscow_no_session_shift(tmp_path):
    tmp = str(tmp_path)
    _site(tmp, {"data.json": {"meta": {"price_asof": "2026-07-10"}}})
    # now = Sat 01:00 MSK == Fri 22:00 UTC — дата сессии не съезжает
    b = bss.build_status(now_utc=utc_of(2026, 7, 11, 1, 0), site_dir=tmp)["blocks"]["market"]
    assert b["source_as_of"] == "2026-07-10"
    assert b["market_session_date"] == "2026-07-10"


def test_3_first_trading_day_after_holidays_before_price(tmp_path):
    tmp = str(tmp_path)
    # 9 янв 09:00 (до открытия): данные за 31 дек — последняя завершённая сессия
    _site(tmp, {"data.json": {"meta": {"price_asof": "2025-12-31"}}})
    b = bss.build_status(now_utc=utc_of(2026, 1, 9, 9, 0), site_dir=tmp)["blocks"]["market"]
    assert b["source_as_of"] == "2025-12-31"
    assert b["freshness_status"] == "market_closed_current"
    assert b["status"] == "fresh"
