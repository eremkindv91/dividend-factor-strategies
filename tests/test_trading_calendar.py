# -*- coding: utf-8 -*-
"""Тесты торгового календаря MOEX (scripts/trading_calendar.py). Всё детерминированно —
фиксированные даты/время, без зависимости от текущей даты запуска и локальной timezone."""
import os
import sys
from datetime import date, datetime, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import trading_calendar as tc  # noqa: E402


def msk(y, mo, d, h=12, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=tc.MSK)


# ── торговые/неторговые дни ──
def test_weekend_is_not_trading():
    assert not tc.is_trading_day(date(2026, 7, 11))   # Sat
    assert not tc.is_trading_day(date(2026, 7, 12))   # Sun
    assert tc.is_trading_day(date(2026, 7, 10))       # Fri


def test_fixed_holidays_closed():
    for d in [date(2026, 1, 1), date(2026, 2, 23), date(2026, 3, 8),
              date(2026, 5, 1), date(2026, 5, 9), date(2026, 6, 12), date(2026, 11, 4)]:
        assert not tc.is_trading_day(d), d


def test_long_january_holidays_1_to_8_closed():
    for day in range(1, 9):                           # 1–8 января — статутарные каникулы
        assert not tc.is_trading_day(date(2026, 1, day)), day
    assert tc.is_trading_day(date(2026, 1, 9))        # 9 января — Fri, первый торговый день


def test_prev_next_trading_day_skips_weekend():
    assert tc.prev_trading_day(date(2026, 7, 13)) == date(2026, 7, 10)   # Mon → prev Fri
    assert tc.next_trading_day(date(2026, 7, 10)) == date(2026, 7, 13)   # Fri → next Mon


def test_next_trading_day_after_long_holidays():
    # 31 дек 2025 (Wed) торговый → следующий торговый 9 янв 2026 (Fri), 1–8 закрыто
    assert tc.next_trading_day(date(2025, 12, 31)) == date(2026, 1, 9)


# ── последняя завершённая сессия ──
def test_last_completed_session_during_open():
    # пятница 11:00 — сессия идёт, последняя ЗАВЕРШЁННАЯ = четверг
    assert tc.last_completed_session(msk(2026, 7, 10, 11, 0)) == date(2026, 7, 9)


def test_last_completed_session_after_close():
    assert tc.last_completed_session(msk(2026, 7, 10, 19, 30)) == date(2026, 7, 10)


def test_last_completed_session_weekend_holds_friday():
    assert tc.last_completed_session(msk(2026, 7, 11, 12, 0)) == date(2026, 7, 10)  # Sat
    assert tc.last_completed_session(msk(2026, 7, 12, 12, 0)) == date(2026, 7, 10)  # Sun


def test_last_completed_session_monday_before_open():
    assert tc.last_completed_session(msk(2026, 7, 13, 8, 0)) == date(2026, 7, 10)


def test_last_completed_session_first_day_after_holidays_before_open():
    # 9 янв 09:00 (до открытия) — последняя завершённая = 31 дек
    assert tc.last_completed_session(msk(2026, 1, 9, 9, 0)) == date(2025, 12, 31)


# ── сессии между ──
def test_sessions_between_zero_on_weekend():
    assert tc.sessions_between(date(2026, 7, 10), msk(2026, 7, 12, 12, 0)) == 0


def test_sessions_between_one_after_close():
    assert tc.sessions_between(date(2026, 7, 9), msk(2026, 7, 10, 19, 30)) == 1


def test_sessions_between_two():
    assert tc.sessions_between(date(2026, 7, 8), msk(2026, 7, 10, 19, 30)) == 2


# ── session open ──
def test_is_session_open():
    assert tc.is_session_open(msk(2026, 7, 10, 11, 0))
    assert not tc.is_session_open(msk(2026, 7, 10, 9, 0))    # до открытия
    assert not tc.is_session_open(msk(2026, 7, 10, 19, 0))   # после закрытия
    assert not tc.is_session_open(msk(2026, 7, 12, 11, 0))   # воскресенье


# ── timezone / парсинг ──
def test_parse_asof_naive_treated_as_utc():
    d, dt = tc.parse_asof("2026-07-10T21:30:00")   # UTC 21:30 → MSK 00:30 next day
    assert d == date(2026, 7, 11)
    assert dt.tzinfo is not None


def test_parse_asof_date_only_no_forward_shift():
    d, _ = tc.parse_asof("2026-07-10")
    assert d == date(2026, 7, 10)                  # чистая дата не съезжает вперёд


def test_parse_asof_invalid_returns_none():
    assert tc.parse_asof("not-a-date") == (None, None)
    assert tc.parse_asof(None) == (None, None)
    assert tc.parse_asof("") == (None, None)


def test_utc_moscow_boundary_no_session_shift():
    # данные с as_of на пятницу; «сейчас» = UTC-время, попадающее в субботу МСК —
    # день сессии не должен съехать
    now = msk(2026, 7, 11, 1, 0)                    # Sat 01:00 MSK == Fri 22:00 UTC
    assert tc.last_completed_session(now) == date(2026, 7, 10)


def test_first_slot_after():
    slots = [time(9, 0), time(19, 0)]
    # после закрытия пятницы 18:50 первый слот = пт 19:00
    got = tc.first_slot_after(datetime.combine(date(2026, 7, 10), time(18, 50), tzinfo=tc.MSK), slots)
    assert got == datetime.combine(date(2026, 7, 10), time(19, 0), tzinfo=tc.MSK)


def test_overrides_absent_uses_core(monkeypatch):
    # при отсутствии data/moex_calendar.json работает fallback-правило (будни = торговые)
    tc._overrides_cache = None
    monkeypatch.setattr(tc, "_OVERRIDES_PATH", "/nonexistent/moex_calendar.json")
    assert tc._overrides() == {"extra_holidays": set(), "extra_trading": set()}
    assert tc.is_trading_day(date(2026, 7, 10))
    tc._overrides_cache = None
