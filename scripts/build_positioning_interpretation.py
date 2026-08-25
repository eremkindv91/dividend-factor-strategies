#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Интерпретация позиционирования физлиц по индексу МосБиржи.

    futures_positions.json + market_history.json
        -> детерминированные признаки
        -> детерминированный классификатор режима
        -> текст по правилам
        -> (опционально) LLM как РЕДАКТОР уже готового текста
        -> site/market_positioning_commentary.json

ПОЧЕМУ РЕЖИМ СЧИТАЕТ КОД, А НЕ МОДЕЛЬ. Одно и то же изменение Net получается из
противоположных процессов: Net растёт и когда открывают новые длинные, и когда закрывают
короткие. Языковая модель, увидев «Net +27 446», выберет объяснение по звучанию, а не по
арифметике — и половину времени будет называть закрытие шортов набором лонгов. Поэтому
режим выводится из ΔLong и ΔShort здесь, в коде, и результат модели менять не разрешено.

ЧТО МОДЕЛЬ ДЕЛАЕТ. Только переписывает готовую формулировку короче и живее. Ей не
передаются сырые ряды, она не получает права поменять режим и не вставляет числа —
все цифры рисует фронтенд из блока facts. Ответ проверяется: схема, длина, отсутствие
запрещённых оборотов и любых новых чисел. Не прошёл — публикуется правило.

БЕЗ КЛЮЧА ВСЁ РАБОТАЕТ. Текст по правилам — не заглушка, а основной production-слой:
интерфейс с ним выглядит законченным, и падение провайдера не роняет сборку.

ЧЕГО ЗДЕСЬ НЕТ И НЕ БУДЕТ БЕЗ ОТДЕЛЬНОГО ИССЛЕДОВАНИЯ. Ни BUY/SELL, ни прогноза индекса,
ни «толпа всегда неправа». Открытые позиции наблюдаемы; мотивы участников и будущая цена —
нет. Проверка предсказательной силы — отдельная задача с walk-forward валидацией, до неё
комментарий остаётся описательным.

Только stdlib.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
POSITIONS = SITE / "futures_positions.json"
HISTORY = SITE / "market_history.json"
OUT = SITE / "market_positioning_commentary.json"

ENGINE_VERSION = "1"
PROMPT_VERSION = "positioning-v1"
INSTRUMENT = "IMOEX"

# ── Пороги. Собраны здесь, а не разбросаны по коду: это продуктовые решения, которые
#    должны меняться в одном месте и попадать в тесты. Ни один из них не является
#    торговым правилом.
NEUTRAL_BAND = 0.02        # |net_ratio| ниже — считаем позиционирование нейтральным
SIGNIFICANT_SHARE = 0.005  # изменение стороны меньше 0.5% от gross — шум, а не движение
DOMINANCE = 0.65           # вклад стороны выше 65% — она и объясняет изменение Net
PRICE_FLAT_BAND = 0.005    # доходность в пределах ±0.5% — «цена на месте»
STRENGTH_MEDIUM = 0.5      # |robust z| изменения Net: граница слабого и заметного
STRENGTH_STRONG = 1.5      # ... заметного и сильного

FORBIDDEN = [
    "buy", "sell", "покупа", "продава", "продать", "купить", "smart money", "умные деньги",
    "толпа", "развернётся", "развернется", "вырастет", "упадёт", "упадет", "прогноз",
    "рекоменд", "сигнал", "уверенность", "перекуплен", "перепродан", "гарантир",
]

LIMITS = {"headline": 90, "summary": 260, "watch": 180}


def log(msg: str) -> None:
    print(f"[positioning] {msg}")


# ─────────────────────────── признаки ───────────────────────────


def price_returns(series: list, as_of: str) -> tuple[dict, str | None]:
    """Доходности индекса за 1/5/20 наблюдений на дату НЕ ПОЗЖЕ as_of.

    Отсечка по дате обязательна: позиции публикуются за предыдущий торговый день, а цена
    доступна за сегодняшний. Взять свежую цену к вчерашним позициям — это look-ahead,
    то есть объяснение вчерашнего поведения тем, чего вчера ещё не произошло.
    """
    rows = [r for r in series if r and r[0] and r[0] <= as_of and r[4] is not None]
    if not rows:
        return {}, None
    closes = [float(r[4]) for r in rows]
    out = {}
    for days, key in ((1, "price_return_1d"), (5, "price_return_5d"), (20, "price_return_20d")):
        if len(closes) > days and closes[-1 - days]:
            out[key] = round(closes[-1] / closes[-1 - days] - 1, 5)
        else:
            out[key] = None
    return out, rows[-1][0]


def position_features(index: dict, as_of: str) -> dict | None:
    """Признаки позиций НА ДАТУ as_of, посчитанные из ряда, а не взятые из summary.

    summary описывает последнюю точку ряда. Если цена доступна по более раннюю дату,
    брать признаки из summary нельзя: тогда изменение позиций за пять дней относилось бы
    к одному отрезку времени, а доходность индекса — к другому, и вся связка «цена против
    позиционирования» сравнивала бы разные недели.
    """
    dates = index.get("dates") or []
    cut = [i for i, d in enumerate(dates) if d <= as_of]
    if not cut:
        return None
    last = cut[-1]
    longs, shorts = index.get("long") or [], index.get("short") or []
    persons_long = index.get("persons_long") or []
    persons_short = index.get("persons_short") or []
    if last >= len(longs) or last >= len(shorts):
        return None

    def at(i: int) -> dict:
        return {
            "long": longs[i],
            "short": shorts[i],
            "persons_long": persons_long[i] if i < len(persons_long) else None,
            "persons_short": persons_short[i] if i < len(persons_short) else None,
        }

    def change(days: int, key: str) -> int | None:
        if last - days < 0:
            return None
        now_, was = at(last), at(last - days)
        pick = (lambda r: r["long"] - r["short"]) if key == "net" else \
               (lambda r: r["long"] + r["short"]) if key == "gross" else \
               (lambda r: r[key])
        current, previous = pick(now_), pick(was)
        return current - previous if current is not None and previous is not None else None

    long, short = longs[last], shorts[last]
    gross = long + short
    return {
        "as_of": dates[last], "long": long, "short": short, "net": long - short, "gross": gross,
        "net_ratio": round((long - short) / gross, 4) if gross else None,
        "persons_long": persons_long[last] if last < len(persons_long) else None,
        "persons_short": persons_short[last] if last < len(persons_short) else None,
        "persons_long_change_5d": change(5, "persons_long"),
        "persons_short_change_5d": change(5, "persons_short"),
        "delta_long_5d": change(5, "long"), "delta_short_5d": change(5, "short"),
        "delta_net_5d": change(5, "net"), "delta_gross_5d": change(5, "gross"),
        "delta_net_1d": change(1, "net"), "delta_net_20d": change(20, "net"),
    }


def net_state(net_ratio: float | None) -> str:
    if net_ratio is None:
        return "unknown"
    if abs(net_ratio) < NEUTRAL_BAND:
        return "neutral"
    return "net_long" if net_ratio > 0 else "net_short"


def flow_regime(d_long: int | None, d_short: int | None, gross: int | None) -> str:
    """Что именно происходило со сторонами — причина изменения Net, а не его знак."""
    if d_long is None or d_short is None or not gross:
        return "unknown"
    floor = gross * SIGNIFICANT_SHARE
    long_moves, short_moves = abs(d_long) >= floor, abs(d_short) >= floor
    if not long_moves and not short_moves:
        return "stable"
    if long_moves and short_moves:
        if d_long > 0 and d_short > 0:
            return "two_sided_expansion"
        if d_long < 0 and d_short < 0:
            return "deleveraging"
        # Стороны разошлись: Net меняют обе, объясняет та, чей вклад больше.
        total = abs(d_long) + abs(d_short)
        if abs(d_long) / total >= DOMINANCE:
            return "long_building" if d_long > 0 else "long_unwinding"
        if abs(d_short) / total >= DOMINANCE:
            return "short_building" if d_short > 0 else "short_covering"
        return "mixed"
    if long_moves:
        return "long_building" if d_long > 0 else "long_unwinding"
    return "short_building" if d_short > 0 else "short_covering"


def price_context(price_return_5d: float | None, d_net: int | None) -> str:
    if price_return_5d is None or d_net is None:
        return "unknown"
    if price_return_5d > PRICE_FLAT_BAND:
        price = "up"
    elif price_return_5d < -PRICE_FLAT_BAND:
        price = "down"
    else:
        price = "flat"
    return f"price_{price}_net_{'up' if d_net > 0 else 'down' if d_net < 0 else 'flat'}"


def flow_strength(robust_z: float | None) -> str:
    if robust_z is None:
        return "unknown"
    z = abs(robust_z)
    if z < STRENGTH_MEDIUM:
        return "weak"
    return "medium" if z < STRENGTH_STRONG else "strong"


# ─────────────────────────── текст по правилам ───────────────────────────

REGIME_COPY = {
    "long_building": ("Физлица наращивают длинные позиции",
                      "Чистая позиция растёт прежде всего за счёт новых длинных."),
    "short_covering": ("Физлица закрывают короткие позиции",
                       "Чистая позиция улучшается за счёт закрытия коротких, а не набора длинных."),
    "short_building": ("Физлица наращивают короткие позиции",
                       "Чистая позиция снижается прежде всего за счёт новых коротких."),
    "long_unwinding": ("Физлица сокращают длинные позиции",
                       "Чистая позиция снижается за счёт закрытия длинных, а не набора коротких."),
    "two_sided_expansion": ("Позиции растут с обеих сторон",
                            "Длинные и короткие увеличиваются одновременно, поэтому направленный "
                            "смысл одной чистой позиции здесь слабее."),
    "deleveraging": ("Физлица сокращают позиции с обеих сторон",
                     "Изменение чистой позиции связано с общей разгрузкой, а не с новым "
                     "направленным риском."),
    "mixed": ("Стороны меняются разнонаправленно",
              "Ни длинные, ни короткие не объясняют изменение чистой позиции сами по себе."),
    "stable": ("Позиционирование почти не меняется",
               "За неделю ни одна из сторон заметно не сдвинулась."),
    "unknown": ("Данных для разбора недостаточно",
                "Не хватает наблюдений, чтобы разложить изменение на длинные и короткие."),
}

STATE_COPY = {"net_long": "в нетто-лонге", "net_short": "в нетто-шорте",
              "neutral": "около нуля", "unknown": "н/д"}

PRICE_COPY = {
    "price_up_net_up": "Рост индекса сопровождается усилением чистой позиции физлиц.",
    "price_up_net_down": "Рост индекса не сопровождается усилением чистой позиции физлиц.",
    "price_up_net_flat": "Индекс растёт, чистая позиция физлиц стоит на месте.",
    "price_down_net_up": "Физлица улучшают чистую позицию на снижении индекса.",
    "price_down_net_down": "Снижение индекса сопровождается ухудшением чистой позиции физлиц.",
    "price_down_net_flat": "Индекс снижается, чистая позиция физлиц стоит на месте.",
    "price_flat_net_up": "Индекс почти не изменился, чистая позиция физлиц выросла.",
    "price_flat_net_down": "Индекс почти не изменился, чистая позиция физлиц снизилась.",
    "price_flat_net_flat": "И индекс, и чистая позиция физлиц почти не изменились.",
}

STRENGTH_COPY = {"weak": "Изменение слабое относительно собственной истории.",
                 "medium": "Изменение заметное относительно собственной истории.",
                 "strong": "Изменение сильное относительно собственной истории.",
                 "unknown": ""}

PERCENTILE_COPY = [(10, "Текущее значение крайне низкое для последнего года."),
                   (20, "Текущее значение низкое для последнего года."),
                   (80, "Текущее значение в обычном для года диапазоне."),
                   (90, "Текущее значение высокое для последнего года."),
                   (101, "Текущее значение крайне высокое для последнего года.")]


def rule_copy(state: str, regime: str, context: str, strength: str,
              percentile: float | None, gross_z: float | None) -> dict:
    head_regime, summary_regime = REGIME_COPY[regime]
    headline = f"{head_regime}, оставаясь {STATE_COPY[state]}" \
        if state in ("net_long", "net_short") else head_regime
    parts = [summary_regime]
    if context in PRICE_COPY:
        parts.append(PRICE_COPY[context])
    summary = " ".join(parts)

    watch = []
    if STRENGTH_COPY.get(strength):
        watch.append(STRENGTH_COPY[strength])
    if percentile is not None:
        watch.append(next(text for bound, text in PERCENTILE_COPY if percentile < bound))
    # Про gross говорим только когда он реально расходится с Net: иначе это лишний шум,
    # но когда обе стороны растут вместе, одна чистая позиция скрывает половину картины.
    if gross_z is not None and abs(gross_z) >= STRENGTH_STRONG and regime in (
            "two_sided_expansion", "deleveraging", "mixed", "stable"):
        watch.append("Суммарный объём позиций меняется сильнее, чем чистая позиция.")
    return {"headline": headline[:LIMITS["headline"]],
            "summary": summary[:LIMITS["summary"]],
            "watch": " ".join(watch)[:LIMITS["watch"]]}


# ─────────────────────────── LLM как редактор ───────────────────────────

SYSTEM_PROMPT = (
    "Ты финансовый редактор интерфейса, а не трейдер и не прогнозная модель. "
    "Тебе переданы уже рассчитанные факты и уже определённый режим позиционирования. "
    "Ты не имеешь права менять режим или добавлять новую рыночную гипотезу.\n"
    "Пиши по-русски. Заголовок до 9 слов. Основной текст максимум 2 коротких предложения. "
    "Используй только факты входного JSON. Чётко различай набор длинных, закрытие коротких, "
    "набор коротких и сокращение длинных. Не давай инвестиционных рекомендаций. "
    "Не используй BUY/SELL, «скоро вырастет», «скоро упадёт», «smart money», «толпа ошибается». "
    "Не придумывай числа — вообще не вставляй цифры в текст. "
    "Возвращай только JSON вида {\"headline\": \"...\", \"summary\": \"...\", \"watch\": \"...\"}."
)


def has_digits(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def validate_llm(payload: dict) -> tuple[dict | None, str]:
    """Пропускает ответ модели, только если он безопаснее не стал."""
    if not isinstance(payload, dict):
        return None, "ответ не объект"
    out = {}
    for key, limit in LIMITS.items():
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            return None, f"{key}: не строка"
        value = value.strip()
        if len(value) > limit:
            return None, f"{key}: длиннее {limit}"
        if "<" in value or "](" in value or "**" in value:
            return None, f"{key}: разметка в тексте"
        low = value.lower()
        hit = next((w for w in FORBIDDEN if w in low), None)
        if hit:
            return None, f"{key}: запрещённый оборот «{hit}»"
        # Числа не разрешены вовсе: сверять придуманную цифру с фактами дороже и
        # ненадёжнее, чем просто не пускать её в текст. Все числа рисует фронтенд.
        if has_digits(value):
            return None, f"{key}: число в тексте"
        out[key] = value
    return out, ""


def _post_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_json(text: str) -> dict:
    """Модели любят обрамлять JSON пояснениями и ```-заборами — берём сам объект."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("в ответе нет JSON")
    return json.loads(text[start:end + 1])


# Адаптеры провайдеров изолированы от движка режимов: смена провайдера не должна
# трогать классификатор. Оба — на urllib, потому что этот пайплайн ходит без pip install
# (новостной контур использует SDK google-genai, здесь такой зависимости нет).

def _call_gemini(brief: dict, key: str, model: str, timeout: int) -> dict:
    # Ключ идёт заголовком, а не query-параметром: URL оседают в логах прокси, CDN и
    # диагностики, и секрет в строке запроса рано или поздно окажется в чужом файле.
    data = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"parts": [{"text": json.dumps(brief, ensure_ascii=False)}]}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "temperature": 0.2, "maxOutputTokens": 800},
        }, {"x-goog-api-key": key}, timeout)
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = "".join(p.get("text", "") for p in parts)
    if not text.strip():
        raise ValueError("пустой ответ")
    return _extract_json(text)


def _call_anthropic(brief: dict, key: str, model: str, timeout: int) -> dict:
    data = _post_json(
        "https://api.anthropic.com/v1/messages",
        {"model": model, "max_tokens": 600, "system": SYSTEM_PROMPT,
         "messages": [{"role": "user", "content": json.dumps(brief, ensure_ascii=False)}]},
        {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout)
    text = "".join(b.get("text", "") for b in data.get("content", []))
    return _extract_json(text)


PROVIDERS = {"gemini": (_call_gemini, "gemini-3.5-flash"),
             "anthropic": (_call_anthropic, "claude-sonnet-5")}


def call_llm(brief: dict, timeout: int = 25) -> tuple[dict | None, str]:
    """Редактирует текст у выбранного провайдера. Любая ошибка — причина фолбэка, не сбой."""
    key = os.environ.get("POSITIONING_LLM_API_KEY", "").strip()
    if not key:
        return None, "ключ не задан"
    provider = (os.environ.get("POSITIONING_LLM_PROVIDER") or "gemini").strip().lower()
    if provider not in PROVIDERS:
        return None, f"провайдер «{provider}» не поддерживается"
    call, default_model = PROVIDERS[provider]
    model = os.environ.get("POSITIONING_LLM_MODEL", "").strip() or default_model

    for attempt in range(2):
        try:
            return validate_llm(call(brief, key, model, timeout))
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError) as exc:
            # Ключ и тело запроса в лог не попадают: текст ошибки провайдера может
            # содержать эхо запроса, а лог сборки публичный.
            reason = f"{type(exc).__name__}"
            if isinstance(exc, urllib.error.HTTPError):
                reason = f"HTTP {exc.code}"
            if attempt:
                return None, f"{provider}: {reason}"
    return None, f"{provider}: недоступен"


# ─────────────────────────── сборка ───────────────────────────


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log(f"не прочитан {path.name}: {exc}")
        return None


def build(now: datetime | None = None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    positions = load(POSITIONS)
    index = ((positions or {}).get("indices") or {}).get(INSTRUMENT) or {}
    summary = index.get("summary") or {}

    history = load(HISTORY) or {}
    instrument = next((i for i in (history.get("instruments") or [])
                       if i.get("id") == INSTRUMENT), None)
    position_dates = [str(day)[:10] for day in (index.get("dates") or []) if day]
    price_series = (instrument or {}).get("series") or []
    price_dates = [str(row[0])[:10] for row in price_series if row and row[0] and row[4] is not None]
    position_latest = max(position_dates, default=None)
    price_latest = max(price_dates, default=None)
    common_dates = sorted(set(position_dates) & set(price_dates))
    if not common_dates:
        log("нет общего завершённого торгового дня IMOEX и FUTOI")
        return {
            "meta": {
                "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "unavailable",
                "analysis_date": None,
                "as_of": None,
                "position_latest": position_latest,
                "price_latest": price_latest,
                "position_fallback_used": bool(index.get("fallback_used")),
                "price_fallback_used": bool((instrument or {}).get("fallback_used")),
                "analysis_lag_trading_sessions": None,
                "source": "MOEX ISS openpositions + IMOEX index daily history",
                "warning": "No common completed trading date",
            },
            INSTRUMENT: {"status": "unavailable", "facts": {}, "copy": {}},
        }
    as_of = common_dates[-1]
    prices, price_as_of = price_returns(price_series, as_of)

    # Оба ряда приводятся к одной дате. Если цена отстала, позиции пересчитываются на её
    # дату — иначе «цена против позиционирования» сравнивала бы разные недели.
    position = position_features(index, as_of) or {}
    if position and position["as_of"] != summary.get("as_of"):
        log(f"позиции пересчитаны на дату цены: {position['as_of']} "
            f"(последние доступные — {summary.get('as_of')})")

    facts = {
        "long": position.get("long", summary.get("long")),
        "short": position.get("short", summary.get("short")),
        "net": position.get("net", summary.get("net")),
        "gross": position.get("gross", summary.get("gross")),
        "delta_long_5d": position.get("delta_long_5d", summary.get("long_change_5d")),
        "delta_short_5d": position.get("delta_short_5d", summary.get("short_change_5d")),
        "delta_net_5d": position.get("delta_net_5d", summary.get("change_5d")),
        "delta_gross_5d": position.get("delta_gross_5d", summary.get("gross_change_5d")),
        "delta_net_1d": position.get("delta_net_1d", summary.get("change_1d")),
        "delta_net_20d": position.get("delta_net_20d", summary.get("change_20d")),
        "persons_long": position.get("persons_long", summary.get("persons_long")),
        "persons_short": position.get("persons_short", summary.get("persons_short")),
        "persons_long_change_5d": position.get(
            "persons_long_change_5d", summary.get("persons_long_change_5d")
        ),
        "persons_short_change_5d": position.get(
            "persons_short_change_5d", summary.get("persons_short_change_5d")
        ),
        "net_ratio": position.get("net_ratio", summary.get("net_ratio")),
        "percentile_1y": summary.get("percentile"),
        "net_change_5d_robust_z": summary.get("net_change_5d_robust_z"),
        "gross_change_5d_robust_z": summary.get("gross_change_5d_robust_z"),
        "net_rub": summary.get("net_rub"),
        **prices,
    }

    if position and position["as_of"] != summary.get("as_of"):
        # Эти величины посчитаны для последней точки ряда и к сдвинутой дате не относятся.
        facts["percentile_1y"] = None
        facts["net_change_5d_robust_z"] = None
        facts["gross_change_5d_robust_z"] = None
        facts["net_rub"] = None

    state = net_state(facts["net_ratio"])
    regime = flow_regime(facts["delta_long_5d"], facts["delta_short_5d"], facts["gross"])
    context = price_context(facts.get("price_return_5d"), facts["delta_net_5d"])
    strength = flow_strength(facts["net_change_5d_robust_z"])
    rule = rule_copy(state, regime, context, strength,
                     facts["percentile_1y"], facts["gross_change_5d_robust_z"])

    brief = {"instrument": INSTRUMENT, "as_of": as_of, "net_state": state,
             "flow_regime": regime, "price_context": context, "flow_strength": strength,
             "approved_interpretation": rule}
    input_hash = "sha256:" + hashlib.sha256(
        json.dumps(brief, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    # Ключ кэша шире, чем данные: он включает провайдера, модель и версию промпта.
    # По одним данным кэш попадал бы и после смены модели, и — что хуже — после включения
    # LLM: текст, написанный правилами при выключенном флаге, имел бы тот же хеш, и модель
    # не вызвали бы никогда. Поэтому же переиспользуется только УСПЕШНЫЙ вызов: аварийный
    # фолбэк, случившийся при недоступном провайдере, иначе закэшировался бы навсегда.
    provider_name = (os.environ.get("POSITIONING_LLM_PROVIDER") or "gemini").strip().lower()
    model_name = (os.environ.get("POSITIONING_LLM_MODEL", "").strip()
                  or (PROVIDERS.get(provider_name) or (None, None))[1])
    llm_cache_key = "sha256:" + hashlib.sha256(json.dumps(
        {"brief": brief, "prompt_version": PROMPT_VERSION,
         "provider": provider_name, "model": model_name},
        ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    copy, llm_used, provider, model, reason = rule, False, None, None, "LLM выключен"
    if os.environ.get("POSITIONING_LLM_ENABLED", "").lower() in ("1", "true", "yes"):
        previous_meta = (load(OUT) or {}).get("meta") or {}
        previous_copy = ((load(OUT) or {}).get(INSTRUMENT) or {}).get("copy")
        reusable = (previous_meta.get("llm_used") is True
                    and previous_meta.get("llm_cache_key") == llm_cache_key
                    and previous_copy)
        if reusable:
            copy, llm_used = previous_copy, True
            provider, model = previous_meta.get("provider"), previous_meta.get("model")
            reason = "тот же вход и та же модель — прошлый текст модели"
        else:
            edited, why = call_llm(brief)
            if edited:
                copy, llm_used = edited, True
                provider, model = provider_name, model_name
                reason = "текст отредактирован моделью"
            else:
                reason = f"фолбэк на правила: {why}"
    log(reason)

    later_sessions = sorted({day for day in position_dates + price_dates if day > as_of})
    position_fallback = bool(index.get("fallback_used") or index.get("source_status") == "last_good")
    price_fallback = bool((instrument or {}).get("fallback_used"))
    status = "degraded" if position_fallback or price_fallback or later_sessions else "complete"
    return {
        "meta": {
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": status,
            "analysis_date": as_of,
            "as_of": as_of,
            "position_as_of": position.get("as_of", summary.get("as_of")),
            "position_latest": position_latest,
            "price_as_of": price_as_of,
            "price_latest": price_latest,
            "position_fallback_used": position_fallback,
            "price_fallback_used": price_fallback,
            "analysis_lag_trading_sessions": len(later_sessions),
            "position_freshness": index.get("freshness_status") or index.get("status"),
            "price_instrument": "IMOEX",
            "price_source": (instrument or {}).get("source"),
            "engine_version": ENGINE_VERSION, "prompt_version": PROMPT_VERSION,
            "source": "MOEX ISS openpositions + IMOEX index daily history",
            "input_hash": input_hash, "llm_cache_key": llm_cache_key,
            "llm_used": llm_used, "fallback_used": not llm_used,
            "provider": provider, "model": model, "llm_note": reason,
            "thresholds": {
                "neutral_band": NEUTRAL_BAND, "significant_share": SIGNIFICANT_SHARE,
                "dominance": DOMINANCE, "price_flat_band": PRICE_FLAT_BAND,
                "strength_medium": STRENGTH_MEDIUM, "strength_strong": STRENGTH_STRONG,
            },
            "disclaimer": ("Описание наблюдаемых позиций, а не прогноз и не рекомендация. "
                           "Мотивы участников и будущая цена по этим данным не определяются."),
        },
        INSTRUMENT: {
            "net_state": state, "flow_regime": regime,
            "price_context": context, "flow_strength": strength,
            "facts": facts, "copy": copy, "rule_copy": rule,
        },
    }


def main() -> int:
    payload = build()
    if payload is None:
        return 1
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    block = payload[INSTRUMENT]
    log(f"{block['flow_regime']} · {block['net_state']} · {block['price_context']} "
        f"· {block['flow_strength']} → {OUT.name}")
    log(f"«{block['copy']['headline']}»")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
