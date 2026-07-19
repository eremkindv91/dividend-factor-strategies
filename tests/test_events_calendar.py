# -*- coding: utf-8 -*-
"""Тесты календаря событий: постоянный якорь заседания ЦБ (блок не пуст в дивидендное межсезонье)."""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import update_events_calendar as uec  # noqa: E402


def _dates(events):
    return sorted(e["date"] for e in events)


def test_cbr_meeting_in_window_included():
    today = date(2026, 7, 18)
    ev = uec.build_cbr_events(today - timedelta(days=4), today + timedelta(days=21), today)
    assert "2026-07-24" in _dates(ev)                       # 24.07 в 21-дневном окне
    assert all(e["event_type"] == "cbr_rate_decision" for e in ev)


def test_next_cbr_anchor_beyond_horizon_offseason():
    # межсезонье: 24.07 прошло, ближайшее заседание 11.09 за 21-дневным горизонтом →
    # всё равно включается как постоянный якорь (график ЦБ известен на год вперёд)
    today = date(2026, 8, 5)
    ev = uec.build_cbr_events(today - timedelta(days=4), today + timedelta(days=21), today)
    assert ev, "блок ЦБ не должен быть пустым — есть якорь"
    assert min(_dates(ev)) == "2026-09-11"                  # ближайшее будущее заседание


def test_no_duplicate_when_meeting_in_window():
    today = date(2026, 7, 18)
    ev = uec.build_cbr_events(today - timedelta(days=4), today + timedelta(days=21), today)
    d = _dates(ev)
    assert len(d) == len(set(d))                            # без дублей дат


def test_anchor_advances_after_meeting_passes():
    # после прохождения ближайшего заседания (за пределами back-окна) якорь = следующее
    before = uec.build_cbr_events(date(2026, 7, 20) - timedelta(days=4), date(2026, 7, 20) + timedelta(days=21), date(2026, 7, 20))
    after = uec.build_cbr_events(date(2026, 9, 1) - timedelta(days=4), date(2026, 9, 1) + timedelta(days=21), date(2026, 9, 1))
    assert "2026-07-24" in _dates(before)                   # до — видно 24.07
    assert "2026-07-24" not in _dates(after)                # после (01.09) — 24.07 давно прошло
    assert min(_dates(after)) == "2026-09-11"               # якорь = следующее заседание


def test_all_meetings_past_returns_empty():
    # конец года: все заседания 2026 прошли → пусто (нет будущих в расписании)
    today = date(2026, 12, 31)
    ev = uec.build_cbr_events(today - timedelta(days=4), today + timedelta(days=21), today)
    assert ev == []                                          # честно пусто, не выдумываем


def test_smartlab_discovery_keeps_structured_fields(monkeypatch):
    class FakeSmartlab:
        @staticmethod
        def fetch_dividends(_tickers):
            return [{"ticker": "SBER", "record_date": "2026-07-20", "buy_before": "2026-07-17",
                     "payment_date": "2026-07-30", "dividend": 37.64, "yield": 13.6}]

    monkeypatch.setitem(sys.modules, "fetch_smartlab_dividends", FakeSmartlab)
    events = uec.build_smartlab_dividend_events(
        {"SBER": {"name": "Сбербанк", "mcap": 1}}, {"SBER": 1.0}, date(2026, 7, 10),
        date(2026, 7, 6), date(2026, 7, 31), set())
    registry = next(event for event in events if event["event_type"] == "dividend_registry_close")
    assert registry["data_status"] == "announced"
    assert registry["verification_status"] == "discovery_only"
    assert registry["record_date"] == "2026-07-20"
    assert registry["last_buy_date"] == "2026-07-17"
    assert registry["payment_date"] == "2026-07-30"
    assert registry["dividend_value"] == 37.64
    assert registry["yield_pct"] == 13.6
