#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0: генерирует site/site_status.json — единый статус свежести данных для UI Data Health.

Свежесть считается ОТ ТОРГОВОГО КАЛЕНДАРЯ MOEX (scripts/trading_calendar.py), а НЕ от
календарного возраста: выходной/праздник/закрытый рынок сами по себе не делают данные
устаревшими — устаревание фиксируется только когда реально завершившаяся торговая сессия
не отражена в данных или плановый прогон пайплайна задержался.

Каждый блок получает богатую модель:
  freshness_status ∈ {fresh, market_closed_current, waiting_for_scheduled_update, updating,
                      delayed, stale, partial, unavailable}
  fallback_status  ∈ {none, fallback}
  + generated_at, source_as_of, last_success_at, market_session_date, expected_next_update,
    user_message (человеческий текст без внутренних терминов).
Для обратной совместимости с фронтом сохранены поля: status (грубый: fresh/stale/fallback/broken),
asof, age_days, note, overall.

Один битый блок не роняет остальные. Чистый stdlib. Запускать ПОСЛЕ сборок и restore,
ПЕРЕД публикацией. Выход всегда 0 (это витрина, не гейт).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trading_calendar as tc  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(REPO, "site")

# Слоты плановых прогонов (МСК, пн–пт) — источник истины: cron в .github/workflows.
MARKET_SLOTS = [time(9, 0), time(19, 0)]                 # update.yml
NEWS_SLOTS = [time(7, 0), time(9, 20), time(19, 0)]      # news.yml
GRACE = timedelta(hours=2)   # запас на исполнение пайплайна + пропагацию Pages CDN

# блок → (имя, файл, поля-даты по приоритету, тип источника, параметр)
#   market      — привязан к торговым сессиям MOEX; параметр задаёт допустимый
#                 лаг официального источника в завершённых сессиях;
#   news        — своя частота (утро/контроль/вечер), не наследует статус рынка;
#   periodic    — редко обновляемые ряды (месячные returns, фундаментал): порог в ДНЯХ.
BLOCKS = [
    ("market", "Цены и мультипликаторы", "data.json", ["meta.price_asof", "meta.generated_at"], "market", 0),
    ("marketsaw", "Фаза рынка (MCFTR)", "marketsaw.json", ["data_last", "generated_at"], "market", 1),
    ("market_history", "Графики рынка (MOEX ISS)", "market_history.json", ["data_asof", "generated_at"], "market", 0),
    ("returns", "История доходностей", "returns.json", ["meta.asof"], "periodic", 45),
    ("marlamov", "Форвардная дивдоходность", "marlamov.json", ["meta.source_as_of", "meta.updated", "meta.asof"], "market", 0),
    ("bonds", "Облигации", "bonds/screener.json", ["meta.data_date", "meta.updated", "meta.generated_at"], "market", 0),
    ("cbr", "Банки РФ (ЦБ)", "cbr/valuation.json", ["meta.moex_asof", "meta.generated_at"], "market", 1),
    ("news", "Новости", "news.json", ["generated_at", "date"], "news", None),
    ("events", "События дня", "events_calendar.json", ["meta.generated_at"], "market", 0),
    ("financials", "Фундамент", "site_financials.json", ["meta.generated_at"], "periodic", 60),
]

# грубый статус для CSS/обратной совместимости и для smoke-теста overall ∈ {fresh,stale,fallback}
COARSE = {
    "fresh": "fresh", "market_closed_current": "fresh",
    "waiting_for_scheduled_update": "fresh", "updating": "fresh",
    "delayed": "stale", "stale": "stale", "partial": "stale",
    "fallback": "fallback", "unavailable": "broken",
}
# порядок серьёзности для выбора overall (больше — хуже)
SEVERITY = {
    "fresh": 0, "market_closed_current": 0, "waiting_for_scheduled_update": 1, "updating": 1,
    "fallback": 2, "delayed": 3, "partial": 3, "stale": 4, "unavailable": 5,
}
RU_MONTHS = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def load(name: str):
    full = os.path.join(SITE, name)
    if not os.path.exists(full) or os.path.getsize(full) == 0:
        return None
    try:
        with open(full, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return "broken"


def dig(obj, path: str):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def age_days(s) -> float | None:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 86400
    except ValueError:
        try:
            return float((date.today() - date.fromisoformat(str(s)[:10])).days)
        except ValueError:
            return None


def human_date(d: date | None) -> str:
    return f"{d.day} {RU_MONTHS[d.month]}" if d else "последнюю дату"


# ── классификаторы freshness по типу источника ──────────────────────────────

def classify_market(asof_date: date | None, now_msk: datetime,
                    allowed_session_lag: int = 0) -> str:
    if asof_date is None:                      # нет/битая дата — не выдаём за свежее
        return "stale"
    behind = tc.sessions_between(asof_date, now_msk)
    if behind <= max(0, allowed_session_lag):  # подтверждённый штатный лаг конкретного источника
        return "fresh" if tc.is_session_open(now_msk) else "market_closed_current"
    # завершившаяся сессия не отражена → это тайминг пайплайна, не «старость рынка»
    session = tc.last_completed_session(now_msk)
    ready = tc.first_slot_after(datetime.combine(session, tc.SESSION_CLOSE, tzinfo=tc.MSK), MARKET_SLOTS)
    if now_msk < ready:
        return "waiting_for_scheduled_update"
    if now_msk < ready + GRACE:
        return "updating"
    return "delayed" if behind == 1 else "stale"


def classify_news(asof_dt: datetime | None, now_msk: datetime, has_content: bool) -> str:
    if not has_content:
        return "unavailable"
    if asof_dt is None:
        return "stale"
    last = tc.last_weekday_slot(now_msk, NEWS_SLOTS)
    if asof_dt >= last:                        # дайджест отражает последний плановый слот
        return "fresh"
    if now_msk < last + GRACE:                 # слот только что настал — вероятно, идёт генерация
        return "updating"
    prev = tc.last_weekday_slot(last - timedelta(minutes=1), NEWS_SLOTS)
    return "delayed" if asof_dt >= prev else "stale"


def classify_periodic(asof_raw, now_utc: datetime, thresh_days: float) -> str:
    if not asof_raw:
        return "stale"
    a = age_days_at(asof_raw, now_utc)
    if a is None:
        return "stale"
    return "fresh" if a <= thresh_days else "stale"


def age_days_at(s, now_utc: datetime) -> float | None:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (now_utc - t).total_seconds() / 86400
    except ValueError:
        try:
            return float((now_utc.date() - date.fromisoformat(str(s)[:10])).days)
        except ValueError:
            return None


def user_message(fine: str, asof_date: date | None, src_type: str = "market") -> str:
    dm = human_date(asof_date)
    if fine == "fresh":
        if src_type == "market":
            return "Актуально на последнюю торговую сессию"
        return f"Актуально на {dm}" if asof_date else "Актуально"
    return {
        "fresh": "Актуально",
        "market_closed_current": f"Рынок закрыт. Последние данные — за {dm}",
        "waiting_for_scheduled_update": f"Данные за {dm}. Обновление ожидается по расписанию",
        "updating": "Данные обновляются",
        "delayed": f"Обновление задерживается. Показаны данные за {dm}",
        "stale": f"Данные могут быть неактуальны — за {dm}",
        "partial": "Часть данных временно недоступна",
        "fallback": f"Показаны последние успешно загруженные данные (за {dm})",
        "unavailable": "Данные временно недоступны",
    }.get(fine, f"Данные за {dm}")


def overall_message(fine: str) -> str:
    return {
        "fresh": "данные актуальны",
        "market_closed_current": "рынок закрыт — данные на последнюю сессию",
        "waiting_for_scheduled_update": "ожидается плановое обновление",
        "updating": "идёт обновление",
        "delayed": "обновление задерживается",
        "stale": "часть данных устарела",
        "partial": "часть данных недоступна",
        "fallback": "показаны резервные данные",
        "unavailable": "часть данных недоступна",
    }.get(fine, "данные актуальны")


# поля времени ГЕНЕРАЦИИ файла (когда пересобрано) — отдельно от даты данных (source_as_of)
GEN_FIELDS = ["meta.generated_at", "generated_at", "meta.updated", "meta.обновлено", "meta.checked_at"]


def _first(obj, fields):
    for f in fields:
        v = dig(obj, f)
        if v:
            return v
    return None


def classify_block(cfg, obj, is_fallback: bool, now_utc: datetime) -> dict:
    key, title, fname, date_fields, src_type, param = cfg
    now_msk = tc.to_msk(now_utc)

    if obj is None or obj == "broken":
        return _pack(key, title, src_type, "unavailable", None, None, None, is_fallback, now_msk, False)

    asof_raw = _first(obj, date_fields)                  # ДАТА ДАННЫХ (не время экспорта)
    gen_raw = _first(obj, GEN_FIELDS)                     # время генерации файла — метаданные, отдельно
    asof_date, asof_dt = tc.parse_asof(asof_raw)

    # Отбраковка невалидной даты данных: будущая дата (позже сегодняшней МСК) невозможна для
    # фактического наблюдения → не выдаём за подтверждённую; НЕ подставляем текущую дату.
    unverified = False
    if asof_date is not None and asof_date > now_msk.date():
        unverified, asof_date, asof_dt, asof_raw = True, None, None, None
    if asof_raw is None and not is_fallback:
        unverified = True                                # источник без даты → «дата не подтверждена»

    if is_fallback:
        fine = "fallback"
    elif src_type == "market":
        fine = classify_market(asof_date, now_msk, int(param or 0))
    elif src_type == "news":
        has_content = bool(obj.get("market_snapshot")) if isinstance(obj, dict) else False
        fine = classify_news(asof_dt, now_msk, has_content)
    else:  # periodic
        fine = classify_periodic(asof_raw, now_utc, float(param))

    return _pack(key, title, src_type, fine, asof_raw, asof_date, asof_dt, is_fallback, now_msk,
                 unverified, gen_raw)


def _pack(key, title, src_type, fine, asof_raw, asof_date, asof_dt, is_fallback, now_msk,
          unverified=False, gen_raw=None) -> dict:
    coarse = COARSE.get(fine, "stale")
    age = age_days(asof_raw) if asof_raw else None
    session_date = tc.last_completed_session(now_msk).isoformat() if src_type == "market" else None
    if src_type == "market":
        nxt = tc.next_weekday_slot(now_msk, MARKET_SLOTS)
    elif src_type == "news":
        nxt = tc.next_weekday_slot(now_msk, NEWS_SLOTS)
    else:
        nxt = None
    # источник без подтверждённой даты данных → честное сообщение, не подставляем дату
    msg = ("Дата исходных данных не подтверждена" if (unverified and fine != "unavailable")
           else user_message(fine, asof_date, src_type))
    gen19 = str(gen_raw)[:19] if gen_raw else None
    return {
        "title": title,
        "status": coarse,                                    # back-compat (CSS/overall/smoke)
        "freshness_status": fine,
        "fallback_status": "fallback" if is_fallback else "none",
        "asof": asof_date.isoformat() if asof_date else None,   # back-compat: дата ДАННЫХ
        "source_as_of": asof_date.isoformat() if asof_date else None,
        "generated_at": gen19,                               # время пересборки файла (метаданные)
        "last_success_at": gen19,
        "market_session_date": session_date,
        "expected_next_update": nxt.isoformat() if nxt else None,
        "age_days": round(age, 1) if age is not None else None,
        "user_message": msg,
        "note": "",
    }


def build_status(now_utc: datetime | None = None, site_dir: str | None = None) -> dict:
    """Собрать payload site_status.json. now_utc/site_dir инъектируются в тестах."""
    global SITE
    if site_dir:
        SITE = site_dir
    now_utc = now_utc or datetime.now(timezone.utc)

    fb = load("_fallback.json") or {}
    fallback_files = set((fb.get("restored") or [])) if isinstance(fb, dict) else set()

    blocks, worst_fine = {}, "fresh"
    for cfg in BLOCKS:
        key, _title, fname = cfg[0], cfg[1], cfg[2]
        obj = load(fname)
        blk = classify_block(cfg, obj, fname in fallback_files, now_utc)
        blocks[key] = blk
        if SEVERITY.get(blk["freshness_status"], 0) > SEVERITY.get(worst_fine, 0):
            worst_fine = blk["freshness_status"]

    return {
        "generated_at": now_utc.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "overall": COARSE.get(worst_fine, "stale"),
        "overall_status": worst_fine,
        "overall_message": overall_message(worst_fine),
        "blocks": blocks,
        "disclaimer": "Информационно-аналитический материал. Не индивидуальная инвестиционная рекомендация. "
                      "Все данные с источником и датой; резервные данные помечены.",
    }


def main() -> int:
    payload = build_status()
    out = os.path.join(SITE, "site_status.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    sys.stderr.write(f"[site-status] overall={payload['overall']} ({payload['overall_status']}); " +
                     ", ".join(f"{k}:{v['freshness_status']}" for k, v in payload["blocks"].items()) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
