# -*- coding: utf-8 -*-
"""Тесты Status/Freshness Engine (scripts/build_site_status.py). Фиксированное время,
без зависимости от даты запуска. Покрывают 15 сценариев ТЗ."""
import json
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import build_site_status as bss  # noqa: E402
import trading_calendar as tc  # noqa: E402


def msk(y, mo, d, h=12, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=tc.MSK)


# ── рыночные данные: классификатор напрямую (детерминированно) ──

def test_1_monday_before_open():                       # понедельник до открытия
    assert bss.classify_market(date(2026, 7, 10), msk(2026, 7, 13, 8, 0)) == "market_closed_current"


def test_2_session_ongoing():                          # сессия идёт
    assert bss.classify_market(date(2026, 7, 9), msk(2026, 7, 10, 11, 0)) == "fresh"


def test_3_after_close_data_updated():                 # день закрыт, данные обновились
    assert bss.classify_market(date(2026, 7, 10), msk(2026, 7, 10, 21, 30)) == "market_closed_current"


def test_4_saturday_after_friday_snapshot():           # суббота после пятничного snapshot
    assert bss.classify_market(date(2026, 7, 10), msk(2026, 7, 11, 12, 0)) == "market_closed_current"


def test_5_sunday():                                   # воскресенье
    assert bss.classify_market(date(2026, 7, 10), msk(2026, 7, 12, 12, 0)) == "market_closed_current"


def test_6_moex_holiday():                             # праздник MOEX (9 мая, Sat-independent)
    assert bss.classify_market(date(2026, 5, 8), msk(2026, 5, 9, 12, 0)) == "market_closed_current"


def test_7_first_trading_day_after_long_holidays_before_open():
    # 9 янв 09:00 до открытия: данные за 31 дек — последняя завершённая сессия → не stale
    assert bss.classify_market(date(2025, 12, 31), msk(2026, 1, 9, 9, 0)) == "market_closed_current"


def test_8_pipeline_on_time():                         # прогон вовремя (после закрытия, данные свежие)
    assert bss.classify_market(date(2026, 7, 10), msk(2026, 7, 10, 19, 5)) == "market_closed_current"


def test_9_pipeline_delayed():                         # прогон задержался (сессия закрыта, данные вчерашние)
    # 1 сессия позади + прошёл слот+grace → delayed
    assert bss.classify_market(date(2026, 7, 9), msk(2026, 7, 10, 21, 30)) == "delayed"


def test_expected_one_session_source_lag_is_not_stale():
    # MCFTR публикуется MOEX с лагом до одной завершённой сессии.
    assert bss.classify_market(
        date(2026, 7, 9), msk(2026, 7, 10, 21, 30), allowed_session_lag=1
    ) == "market_closed_current"


def test_marketsaw_and_cbr_have_explicit_one_session_allowance():
    allowed = {cfg[0]: cfg[5] for cfg in bss.BLOCKS}
    assert allowed["market"] == 0
    assert allowed["marketsaw"] == 1
    assert allowed["cbr"] == 1


def test_9b_waiting_right_after_close():               # сразу после закрытия — ждём плановый прогон
    assert bss.classify_market(date(2026, 7, 9), msk(2026, 7, 10, 18, 55)) == "waiting_for_scheduled_update"


def test_12_invalid_timestamp_not_fresh():             # некорректный timestamp → не выдаём за свежее
    assert bss.classify_market(None, msk(2026, 7, 10, 12, 0)) == "stale"


def test_13_utc_moscow_boundary():                     # граница UTC/МСК не сдвигает сессию
    now = msk(2026, 7, 11, 1, 0)                        # Sat 01:00 MSK == Fri 22:00 UTC
    assert bss.classify_market(date(2026, 7, 10), now) == "market_closed_current"


def test_15_older_than_last_session():                 # данные старше последней сессии
    assert bss.classify_market(date(2026, 7, 8), msk(2026, 7, 10, 21, 30)) == "stale"


# ── новости: своя частота, не наследуют рынок ──

def test_14_news_weekend_briefing_supersedes_friday():
    # Контракт изменён вместе с появлением ВЫХОДНЫХ выпусков (cron сб 10:23 / вс 17:23).
    # Раньше пятничная сводка считалась свежей всю субботу — но только потому, что
    # субботнего выпуска не существовало и разрыв в 61–62 ч выдавался за норму.
    fri_evening = datetime(2026, 7, 10, 19, 5, tzinfo=tc.MSK)
    # сб 09:00 — субботний слот (10:23) ещё не настал, пятничная сводка честно свежая
    assert bss.classify_news(fri_evening, msk(2026, 7, 11, 9, 0), True) == "fresh"
    # сб 12:00 — слот настал, идёт генерация (в пределах NEWS_GRACE): «обновляется», не «свежо»
    assert bss.classify_news(fri_evening, msk(2026, 7, 11, 12, 0), True) == "updating"
    # сб 16:00 — окно очереди вышло, субботнего выпуска нет: честно «задержан»
    assert bss.classify_news(fri_evening, msk(2026, 7, 11, 16, 0), True) == "delayed"
    # субботний выпуск пришёл — снова свежо
    sat_briefing = datetime(2026, 7, 11, 12, 40, tzinfo=tc.MSK)
    assert bss.classify_news(sat_briefing, msk(2026, 7, 11, 16, 0), True) == "fresh"


def test_news_sunday_briefing_covers_monday_open():
    """Воскресный выпуск (cron 17:23) должен закрывать ночь до понедельника."""
    sun_briefing = datetime(2026, 7, 12, 18, 40, tzinfo=tc.MSK)
    assert bss.classify_news(sun_briefing, msk(2026, 7, 12, 22, 0), True) == "fresh"
    # понедельник 07:00: слот 05:37 настал, очередь ещё может тянуть → «обновляется»
    assert bss.classify_news(sun_briefing, msk(2026, 7, 13, 7, 0), True) == "updating"


def test_news_premarket_slot_lands_before_open():
    """Утренний прогон обязан давать свежую сводку ДО открытия сессии (10:00 МСК).

    Это и есть исходная жалоба: при старом расписании (cron 07:00 + очередь +2.4 ч)
    сводка приезжала в 09:26, а «предторговый контроль» — в 11:54, уже после открытия.
    """
    mon_premarket = datetime(2026, 7, 13, 8, 10, tzinfo=tc.MSK)
    assert bss.classify_news(mon_premarket, msk(2026, 7, 13, 9, 45), True) == "fresh"


def test_news_overnight_reflects_last_slot():
    # ночь вторника 02:00: последний слот — пн 19:00; дайджест от пн 19:05 — свежий
    mon_evening = datetime(2026, 7, 13, 19, 5, tzinfo=tc.MSK)
    assert bss.classify_news(mon_evening, msk(2026, 7, 14, 2, 0), True) == "fresh"


def test_news_unavailable_without_snapshot():
    assert bss.classify_news(datetime(2026, 7, 10, 19, 0, tzinfo=tc.MSK), msk(2026, 7, 10, 20, 0), False) == "unavailable"


# ── периодические (месячные/фундаментал) — не по дневному порогу ──

def test_periodic_month_old_returns_fresh():
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    assert bss.classify_periodic("2026-06-26", now, 45) == "fresh"   # 16 дн < 45 → свежо


def test_periodic_beyond_threshold_stale():
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    assert bss.classify_periodic("2026-01-01", now, 45) == "stale"


# ── интеграция build_status с временными файлами ──

def _write_site(tmp, files):
    for name, obj in files.items():
        p = os.path.join(tmp, name)
        os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(p) != tmp else None
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f)


def test_10_fallback_snapshot(tmp_path):               # есть fallback snapshot
    tmp = str(tmp_path)
    _write_site(tmp, {
        "data.json": {"meta": {"price_asof": "2026-07-10"}},
        "_fallback.json": {"restored": ["data.json"]},
    })
    p = bss.build_status(now_utc=msk(2026, 7, 12, 12, 0).astimezone(timezone.utc), site_dir=tmp)
    blk = p["blocks"]["market"]
    assert blk["freshness_status"] == "fallback"
    assert blk["fallback_status"] == "fallback"
    assert blk["status"] == "fallback"
    assert "успешно загруженные" in blk["user_message"]


def test_11_snapshot_missing(tmp_path):                # snapshot отсутствует
    tmp = str(tmp_path)
    _write_site(tmp, {"data.json": {"meta": {"price_asof": "2026-07-10"}}})  # marketsaw отсутствует
    p = bss.build_status(now_utc=msk(2026, 7, 12, 12, 0).astimezone(timezone.utc), site_dir=tmp)
    assert p["blocks"]["marketsaw"]["freshness_status"] == "unavailable"
    assert p["blocks"]["marketsaw"]["status"] == "broken"
    assert "недоступны" in p["blocks"]["marketsaw"]["user_message"]


def test_weekend_market_data_not_stale_integration(tmp_path):
    # ключевой сценарий бага: воскресенье, свежие пятничные данные ≠ «устаревает»
    tmp = str(tmp_path)
    _write_site(tmp, {
        "data.json": {"meta": {"price_asof": "2026-07-10"}},
        "marketsaw.json": {"data_last": "2026-07-10"},
        "news.json": {"generated_at": "2026-07-10T16:05:00", "market_snapshot": {"x": 1}},
    })
    p = bss.build_status(now_utc=msk(2026, 7, 12, 12, 0).astimezone(timezone.utc), site_dir=tmp)
    assert p["blocks"]["market"]["status"] == "fresh"      # coarse fresh — не «stale»
    assert p["blocks"]["market"]["freshness_status"] == "market_closed_current"
    assert "Рынок закрыт" in p["blocks"]["market"]["user_message"]


def test_no_shared_ttl_message_has_no_tech_terms(tmp_path):
    tmp = str(tmp_path)
    _write_site(tmp, {"data.json": {"meta": {"price_asof": "2026-07-10"}}})
    p = bss.build_status(now_utc=msk(2026, 7, 12, 12, 0).astimezone(timezone.utc), site_dir=tmp)
    for b in p["blocks"].values():
        m = b["user_message"].lower()
        for bad in ("stale", "fallback", "null", "none", "error", "http", "undefined"):
            assert bad not in m, (b["title"], m)
