#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Торговый календарь MOEX (фондовый рынок) — минимальное устойчивое ядро. Чистый stdlib.

Зачем: свежесть рыночных данных считается от ПОСЛЕДНЕЙ ЗАВЕРШЁННОЙ ТОРГОВОЙ СЕССИИ,
а не от календарного возраста. Суббота/воскресенье/праздник не делают пятничные
данные устаревшими (quant-honesty, «Календарь MOEX»).

Источник календаря: официальный календарь торгов MOEX (https://www.moex.com/s371)
и производственный календарь РФ. В ядро CORE_HOLIDAYS_MD включены только даты,
в которые биржа стабильно НЕ торгует из года в год. В перенесённые выходные
производственного календаря (например, понедельник после праздника-воскресенья)
MOEX, как правило, ТОРГУЕТ — такие дни в ядро сознательно не включены.

Уточнения конкретного года (спец-выходные/спец-рабочие дни) — через опциональный
файл data/moex_calendar.json:
    {"extra_holidays": ["YYYY-MM-DD", ...], "extra_trading": ["YYYY-MM-DD", ...]}

Fallback-правило: дата вне ядра и вне overrides → торговый день = будний (пн–пт).
Ошибка календаря на одну сессию по построению даёт мягкое состояние
(waiting/delayed), а не «stale»: потребители считают stale от ≥2 пропущенных
сессий (см. scripts/build_site_status.py).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta, timezone

# У РФ нет летнего времени — фиксированный офсет корректен.
MSK = timezone(timedelta(hours=3), "MSK")

# Завершение основной сессии акций (аукцион закрытия ~18:50 МСК). Вечерняя сессия
# уточняет тот же торговый день, поэтому для статуса «сессия завершена» достаточно 18:50.
SESSION_CLOSE = time(18, 50)
# Открытие основной сессии (10:00 МСК); до этого времени торговый день «ещё не открылся».
SESSION_OPEN = time(10, 0)

# (месяц, день): даты, в которые MOEX стабильно не торгует (см. докстринг про источник).
# Новогодние каникулы 1–8 января — статутарно нерабочие (ТК РФ ст. 112), MOEX закрыта
# весь блок из года в год. Плавающие майские переносы (2–8 мая) — не в ядре: их состав
# меняется по годам, задаётся через data/moex_calendar.json.
CORE_HOLIDAYS_MD = frozenset(
    [(1, d) for d in range(1, 9)] + [   # 1–8 января — длинные новогодние каникулы
        (2, 23), (3, 8),                # День защитника Отечества, Международный женский день
        (5, 1), (5, 9),                 # Праздник Весны и Труда, День Победы
        (6, 12), (11, 4),               # День России, День народного единства
    ])

_OVERRIDES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "moex_calendar.json")
_overrides_cache: dict | None = None


def _overrides() -> dict:
    """{'extra_holidays': set[date], 'extra_trading': set[date]} из data/moex_calendar.json."""
    global _overrides_cache
    if _overrides_cache is None:
        hol, trd = set(), set()
        try:
            with open(_OVERRIDES_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            for s in raw.get("extra_holidays") or []:
                hol.add(date.fromisoformat(str(s)[:10]))
            for s in raw.get("extra_trading") or []:
                trd.add(date.fromisoformat(str(s)[:10]))
        except (OSError, ValueError):
            pass  # нет файла/битый → работаем по ядру (fallback-правило)
        _overrides_cache = {"extra_holidays": hol, "extra_trading": trd}
    return _overrides_cache


def is_trading_day(d: date) -> bool:
    ov = _overrides()
    if d in ov["extra_trading"]:
        return True
    if d in ov["extra_holidays"]:
        return False
    if d.weekday() >= 5:  # сб/вс: сессии выходного дня MOEX аттрибутируются следующему
        return False      # торговому дню и дату данных не двигают
    return (d.month, d.day) not in CORE_HOLIDAYS_MD


def prev_trading_day(d: date) -> date:
    x = d - timedelta(days=1)
    while not is_trading_day(x):
        x -= timedelta(days=1)
    return x


def next_trading_day(d: date) -> date:
    x = d + timedelta(days=1)
    while not is_trading_day(x):
        x += timedelta(days=1)
    return x


def to_msk(dt: datetime) -> datetime:
    """Naive datetime считаем UTC (так пишут генераторы), затем переводим в МСК."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK)


def is_session_open(now_msk: datetime) -> bool:
    """Идёт ли основная сессия акций прямо сейчас (торговый день, 10:00–18:50 МСК)."""
    return is_trading_day(now_msk.date()) and SESSION_OPEN <= now_msk.time() < SESSION_CLOSE


def last_completed_session(now_msk: datetime) -> date:
    """Дата последней ЗАВЕРШЁННОЙ основной сессии на момент now (МСК)."""
    d = now_msk.date()
    if is_trading_day(d) and now_msk.time() >= SESSION_CLOSE:
        return d
    return prev_trading_day(d)


def sessions_between(asof: date, now_msk: datetime, limit: int = 400) -> int:
    """Сколько торговых сессий завершилось СТРОГО ПОСЛЕ даты asof.

    0 → данные соответствуют последней завершённой сессии (или новее — интрадей).
    """
    last = last_completed_session(now_msk)
    if asof >= last:
        return 0
    n, d = 0, asof
    while d < last and n < limit:
        d = next_trading_day(d)
        n += 1
    return n


def parse_asof(s) -> tuple[date | None, datetime | None]:
    """ISO-строка (дата или datetime, naive=UTC) → (дата в МСК, datetime в МСК|None).

    Некорректный/пустой ввод → (None, None) — вызывающий обязан отработать честно.
    """
    if not s:
        return None, None
    txt = str(s).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(txt)
        if isinstance(dt, datetime):
            dt_msk = to_msk(dt)
            return dt_msk.date(), dt_msk
    except ValueError:
        pass
    try:  # чистая дата YYYY-MM-DD: тайзоны нет — берём как дату торгового дня как есть
        return date.fromisoformat(txt[:10]), None
    except ValueError:
        return None, None


def next_weekday_slot(now_msk: datetime, slots: list[time]) -> datetime:
    """Ближайший будущий слот расписания (слоты действуют пн–пт; GH cron не знает
    праздников РФ и запускается в будни всегда — поэтому именно будни, не торговые дни)."""
    d = now_msk.date()
    for _ in range(0, 10):
        if d.weekday() < 5:
            for t in sorted(slots):
                cand = datetime.combine(d, t, tzinfo=MSK)
                if cand > now_msk:
                    return cand
        d += timedelta(days=1)
    return datetime.combine(d, sorted(slots)[0], tzinfo=MSK)


def first_slot_after(dt_msk: datetime, slots: list[time]) -> datetime:
    """Первый слот расписания (пн–пт) строго ПОЗЖЕ dt_msk — «к какому прогону данные должны быть готовы»."""
    d = dt_msk.date()
    for _ in range(0, 10):
        if d.weekday() < 5:
            for t in sorted(slots):
                cand = datetime.combine(d, t, tzinfo=MSK)
                if cand > dt_msk:
                    return cand
        d += timedelta(days=1)
    return datetime.combine(d, sorted(slots)[0], tzinfo=MSK)


def last_weekday_slot(now_msk: datetime, slots: list[time]) -> datetime:
    """Последний прошедший слот расписания (пн–пт)."""
    d = now_msk.date()
    for _ in range(0, 10):
        if d.weekday() < 5:
            for t in sorted(slots, reverse=True):
                cand = datetime.combine(d, t, tzinfo=MSK)
                if cand <= now_msk:
                    return cand
        d -= timedelta(days=1)
    return datetime.combine(d, sorted(slots)[0], tzinfo=MSK)
