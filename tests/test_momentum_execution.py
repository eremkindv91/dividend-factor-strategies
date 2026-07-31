from __future__ import annotations

from datetime import date

from scripts.build_data import momentum_schedule


def test_momentum_schedule_uses_month_end_close_and_next_moex_session():
    schedule = momentum_schedule({"signal_month": "2026-05", "factor_data_through": "2026-04"}, today=date(2026, 6, 15))
    assert schedule["last_signal_at"] == "2026-05-29"
    assert schedule["data_through"] == "2026-04-30"
    assert schedule["last_execution_at"] == "2026-06-01"
    assert schedule["planned_execution_at"] == "2026-07-01"
    assert schedule["next_calculation_at"] == "2026-06-30"
    assert schedule["next_execution_at"] == "2026-07-01"
    assert schedule["timezone"] == "Europe/Moscow"
    assert schedule["trading_days_remaining"] > 0
    assert schedule["calendar_days_remaining"] == 15


def test_momentum_schedule_handles_year_boundary_and_holiday():
    schedule = momentum_schedule({"signal_month": "2026-12", "factor_data_through": "2026-11"}, today=date(2027, 1, 10))
    assert schedule["status"] == "pending_execution"
    assert schedule["last_signal_at"] == "2026-12-31"
    assert schedule["last_execution_at"] is None
    assert schedule["planned_execution_at"] == "2027-01-11"
    assert schedule["next_calculation_at"] == "2027-01-29"
    assert schedule["next_execution_at"] == "2027-02-01"


def test_momentum_schedule_fails_closed_without_signal_month():
    assert momentum_schedule({}, today=date(2026, 6, 1))["status"] == "unavailable"
