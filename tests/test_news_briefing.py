"""Контракт брифинга новостей: тип выпуска, торговое «вчера», устойчивость к сбоям Gemini.

Контекст: до этих правок пайплайн (а) не работал в выходные — разрывы 61–62 ч между
пятничным вечером и утром понедельника, (б) всегда называл выпуск «утренним», даже
вечерний и выходной, (в) считал «вчера» календарным днём, из-за чего в понедельник
опорой становилось воскресенье без торгов, (г) падал целиком на временном 503 у Gemini.
"""
from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MSK = timezone(timedelta(hours=3))


def _load():
    spec = importlib.util.spec_from_file_location("generate_news", ROOT / "news" / "generate_news.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gn = _load()


# ── тип выпуска ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("moment,expected", [
    (datetime(2026, 7, 13, 6, 30, tzinfo=MSK), "premarket"),   # пн до открытия
    (datetime(2026, 7, 13, 9, 59, tzinfo=MSK), "premarket"),   # граница: за минуту до 10:00
    (datetime(2026, 7, 13, 10, 0, tzinfo=MSK), "intraday"),    # ровно открытие
    (datetime(2026, 7, 13, 11, 54, tzinfo=MSK), "intraday"),   # опоздавший прогон — честно «в сессии»
    (datetime(2026, 7, 13, 18, 50, tzinfo=MSK), "evening"),    # ровно закрытие
    (datetime(2026, 7, 13, 20, 23, tzinfo=MSK), "evening"),
    (datetime(2026, 7, 11, 12, 0, tzinfo=MSK), "weekend"),     # суббота
    (datetime(2026, 7, 12, 18, 40, tzinfo=MSK), "week_ahead"), # воскресенье
])
def test_briefing_kind_matches_moment(moment, expected):
    assert gn.briefing_kind(moment) == expected


def test_every_kind_has_title_and_context():
    for kind in ("premarket", "intraday", "evening", "weekend", "week_ahead"):
        title, context = gn.BRIEFING_KINDS[kind]
        assert title and context
        assert "утренн" not in title.lower() or kind == "premarket"


def test_evening_briefing_is_not_called_morning():
    """Регресс-якорь: вечерний выпуск не должен представляться утренним."""
    title, _ = gn.BRIEFING_KINDS[gn.briefing_kind(datetime(2026, 7, 13, 20, 0, tzinfo=MSK))]
    assert "утренн" not in title.lower()


# ── «вчера» = последний ТОРГОВЫЙ день ────────────────────────────────────────

def test_monday_looks_back_to_friday_not_sunday():
    assert gn.last_trading_day(date(2026, 7, 13)) == date(2026, 7, 10)


def test_midweek_looks_back_one_day():
    assert gn.last_trading_day(date(2026, 7, 15)) == date(2026, 7, 14)


def test_saturday_and_sunday_look_back_to_friday():
    assert gn.last_trading_day(date(2026, 7, 11)) == date(2026, 7, 10)
    assert gn.last_trading_day(date(2026, 7, 12)) == date(2026, 7, 10)


def test_last_trading_day_never_returns_weekend():
    probe = date(2026, 7, 1)
    for _ in range(60):
        assert gn.last_trading_day(probe).weekday() < 5
        probe += timedelta(days=1)


def test_prompt_uses_trading_day_and_briefing_type():
    template = ("тип={BRIEFING_TITLE} контекст={BRIEFING_CONTEXT} "
                "сегодня={TODAY} вчера={YESTERDAY} закрытие={RU_CLOSE}")
    rendered = gn.build_prompt(template, datetime(2026, 7, 13, 6, 30, tzinfo=MSK))
    assert "вчера=2026-07-10" in rendered          # пятница, не воскресенье
    assert "закрытие=2026-07-10 18:50 MSK" in rendered
    assert "тип=Сводка до открытия" in rendered
    assert "{" not in rendered.replace("{}", "")   # плейсхолдеров не осталось


# ── устойчивость к временным сбоям Gemini ────────────────────────────────────

@pytest.mark.parametrize("message", [
    "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'high demand'}}",
    "429 RESOURCE_EXHAUSTED",
    "500 INTERNAL",
    "deadline exceeded",
])
def test_transient_errors_are_retryable(message):
    assert gn._is_retryable(RuntimeError(message)) is True


@pytest.mark.parametrize("message", [
    "GEMINI_API_KEY is not set",
    "400 INVALID_ARGUMENT: prompt too long",
    "google-genai is not installed",
])
def test_permanent_errors_are_not_retried(message):
    assert gn._is_retryable(RuntimeError(message)) is False


def test_retry_budget_is_bounded():
    """Ретрай не должен висеть бесконечно: слот новостей ограничен по времени."""
    assert gn.GEMINI_ATTEMPTS == 4
    assert sum(gn.GEMINI_BACKOFF_SEC) <= 180


# ── расписание: контракт с workflow ──────────────────────────────────────────

def test_workflow_covers_weekend_and_avoids_round_minutes():
    """Кроны должны покрывать выходные и стоять на НЕкруглых минутах.

    Круглые минуты (:00, :20) — самые загруженные слоты очереди GitHub; замер
    показал задержку +2.4…+2.6 ч, из-за которой предторговая сводка приходила
    уже после открытия рынка.
    """
    workflow = (ROOT / ".github" / "workflows" / "news.yml").read_text(encoding="utf-8")
    crons = [line.split('"')[1] for line in workflow.splitlines() if "- cron:" in line]
    assert len(crons) == 5
    days = " ".join(c.split()[-1] for c in crons)
    assert "6" in days and "0" in days, "нет субботнего/воскресного слота"
    for cron in crons:
        minute = int(cron.split()[0])
        assert minute % 10 != 0, f"крон {cron} стоит на «круглой» минуте — очередь длиннее"


def test_status_schedule_matches_workflow_crons():
    """Модель свежести обязана знать ровно те слоты, что стоят в workflow."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_site_status as bss  # noqa: E402

    workflow = (ROOT / ".github" / "workflows" / "news.yml").read_text(encoding="utf-8")
    crons = [line.split('"')[1] for line in workflow.splitlines() if "- cron:" in line]
    from_workflow = set()
    for cron in crons:
        minute, hour, _, _, dow = cron.split()
        msk_hour, extra = divmod(int(hour) + 3, 24)   # UTC → МСК
        msk_hour = (int(hour) + 3) % 24
        for day in (range(0, 5) if dow == "1-5" else [int(dow) - 1 if int(dow) > 0 else 6]):
            from_workflow.add((day, msk_hour, int(minute)))
    from_status = {(day, t.hour, t.minute)
                   for day, slots in bss.NEWS_SCHEDULE.items() for t in slots}
    assert from_status == from_workflow, "расписание в build_site_status разошлось с news.yml"


def test_truncated_json_is_retryable():
    """Обрезанный ответ модели — сбой попытки, а не фатальная ошибка прогона.

    Боевой прогон 29.07.2026: ретрай успешно пережил 503 и 429, Gemini ответил
    с 3-й попытки — но ответ пришёл неполным («Unterminated string ... char 6687»),
    и прогон всё равно упал, потому что json.loads стоял ВНЕ цикла ретраев.
    """
    import json as _json
    try:
        _json.loads('{"overnight": [{"headline": "обрыв')
    except _json.JSONDecodeError as exc:
        assert gn._is_retryable(exc) is True
    else:
        pytest.fail("ожидался JSONDecodeError")


def test_output_token_ceiling_is_explicit():
    """Потолок вывода задан явно: без него модель обрывала JSON на середине."""
    source = (ROOT / "news" / "generate_news.py").read_text(encoding="utf-8")
    assert "max_output_tokens=32768" in source
