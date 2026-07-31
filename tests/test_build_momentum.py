from datetime import datetime

from scripts import trading_calendar as tc
from scripts.build_momentum import momentum_signal_window


def test_signal_window_does_not_use_current_month_before_close():
    formation, factor, denominator = momentum_signal_window(
        datetime(2026, 7, 15, 12, 0, tzinfo=tc.MSK)
    )
    assert (formation, factor, denominator) == ("2026-06", "2026-05", "2025-06")


def test_signal_window_rolls_only_after_month_end_close():
    before = momentum_signal_window(datetime(2026, 7, 31, 18, 49, tzinfo=tc.MSK))
    after = momentum_signal_window(datetime(2026, 7, 31, 18, 50, tzinfo=tc.MSK))
    assert before == ("2026-06", "2026-05", "2025-06")
    assert after == ("2026-07", "2026-06", "2025-07")


def test_signal_window_handles_year_boundary():
    assert momentum_signal_window(datetime(2027, 1, 15, 12, 0, tzinfo=tc.MSK)) == (
        "2026-12", "2026-11", "2025-12"
    )
