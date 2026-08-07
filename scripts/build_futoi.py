#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Нетто-позиции физических лиц во фьючерсах на акции РФ (MOEX ISS, analyticalproducts/futoi).

Отвечает на один вопрос: как розничные инвесторы стоят во фьючерсе на бумагу — в лонг
или в шорт, и насколько это отклоняется от собственной истории. Публикуются КОНТРАКТЫ
и направленность внутри позиций физлиц; доля от открытого интереса (% OI) НЕ считается,
пока не доказана семантика знаменателя в источнике.

Только stdlib: workflow update-futoi.yml работает без pip install.

СЕМАНТИКА ИСТОЧНИКА (проверена на живом API, а не по документации)
  • ticker в FUTOI — код базового актива (SR, GZ, RN) и АГРЕГИРУЕТ все экспирации.
    Поэтому выбор front-контракта и rollover здесь не нужны и были бы вредны: они
    создали бы искусственные разрывы там, где источник уже отдаёт непрерывный ряд.
  • clgroup обязателен: FIZ (физлица) против YUR. Ряды строго зеркальны
    (pos_FIZ = −pos_YUR), поэтому публикуется одна сторона.
  • pos — готовая нетто-позиция, инвариант pos == pos_long + pos_short держится
    (проверено на 10 000 строк); pos_short всегда ≤ 0.
  • На дату приходится ~202 внутридневных среза. Берётся ПОСЛЕДНИЙ за день (latest=1),
    иначе точка ряда оказывается случайным моментом внутри сессии.
  • latest=1 ИГНОРИРУЕТ параметр start: каждая страница возвращает один и тот же ответ,
    ограниченный 1000 строками (= 500 дат × 2 группы). Поэтому история берётся окнами
    по календарным годам, а не пагинацией — это ~250 дат в окне, с запасом.
  • Отказ в доступе приходит НЕ как HTTP-ошибка, а строкой ERROR_MESSAGE внутри обычного
    200-ответа. Без явной проверки он читался бы как «в этот период торгов не было».

ЧТО НЕ ВКЛЮЧЕНО И ПОЧЕМУ
  • Вечные фьючерсы (SBERF, GAZPF и др.) в ряд НЕ добавляются, хотя лот у них тот же.
    Они появились в FUTOI между июнем 2024 и июнем 2025: сумма с квартальными дала бы
    структурный скачок в этой точке, который читается как всплеск интереса физлиц,
    хотя это всего лишь запуск нового инструмента. Однородность ряда важнее полноты
    охвата; состав указан в meta явно.
  • Без токена MOEX данные отдаются с задержкой 14 дней. Это не ошибка и не повод
    молчать: дата последнего дня публикуется в meta и обязана показываться в интерфейсе.
    Если в окружении есть MOEX_TOKEN, он подставляется и отсечка снимается.

МАППИНГ АКЦИЯ → КОД FUTOI строится через emitent_id, а не по сходству кодов: у фьючерса
на Сбербанк ASSETCODE = SBRF, тикер FUTOI = SR, тикер акции = SBER — совпадений нет
ни одного, и любое правило «по первым буквам» рано или поздно свяжет чужие бумаги.
"""

from __future__ import annotations

import collections
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "futoi.json"

ISS = "https://iss.moex.com/iss"
UA = "dividend-factor-strategies/futoi (+https://github.com/eremkindv91/dividend-factor-strategies)"
HISTORY_FROM_YEAR = 2020          # начало доступной истории FUTOI
DELAY_DAYS = 14                   # отсечка бесплатного доступа к свежим дням
PAUSE = 0.12                      # пауза между запросами к ISS
PERPETUAL_LAST_TRADE = "2100-01-01"   # маркер вечного фьючерса в списке FORTS
MIN_POINTS = 30                   # короче — ряд не о чем; публикуем статус, а не линию
Z_WINDOW = 250                    # окно для z-score: примерно торговый год


class IssError(RuntimeError):
    """Отказ ISS, пришедший телом ответа, а не HTTP-кодом."""


def http_json(url: str, tries: int = 3) -> dict:
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(0.6 * (attempt + 1))
    raise IssError(f"{url}: {last}")


def block(payload: dict, name: str) -> tuple[list[str], list[list]]:
    b = payload.get(name) or {}
    cols = b.get("columns") or []
    data = b.get("data") or []
    # ISS кладёт отказ в обычный 200-ответ отдельной таблицей с одной колонкой.
    # Принять его за пустой период — значит опубликовать «данных нет» вместо «нет доступа».
    if "ERROR_MESSAGE" in cols:
        message = data[0][cols.index("ERROR_MESSAGE")] if data else "неизвестная причина"
        raise IssError(str(message))
    return cols, data


def rows_as_dicts(payload: dict, name: str) -> list[dict]:
    cols, data = block(payload, name)
    return [dict(zip(cols, row)) for row in data]


# ─────────────────────────── справочник инструментов ───────────────────────────


def futoi_universe() -> set[str]:
    """Коды, по которым источник вообще что-то отдаёт на последнюю доступную дату."""
    payload = http_json(f"{ISS}/analyticalproducts/futoi/securities.json?iss.meta=off")
    return {row["ticker"] for row in rows_as_dicts(payload, "futoi") if row.get("ticker")}


def forts_quarterly_groups() -> dict[str, str]:
    """ASSETCODE → SECID ближайшего квартального контракта (вечные отброшены)."""
    payload = http_json(f"{ISS}/engines/futures/markets/forts/securities.json"
                        "?iss.meta=off&iss.only=securities")
    groups: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for row in rows_as_dicts(payload, "securities"):
        code, secid, last = row.get("ASSETCODE"), row.get("SECID"), row.get("LASTTRADEDATE")
        if code and secid:
            groups[code].append((secid, str(last)))
    out = {}
    for code, items in groups.items():
        quarterly = [(s, d) for s, d in items if d != PERPETUAL_LAST_TRADE]
        if quarterly:
            out[code] = sorted(quarterly, key=lambda x: x[1])[0][0]
    return out


def contract_facts(secid: str) -> dict:
    """EMITTER_ID, тип группы, лот и вид контракта — из описания представителя серии."""
    payload = http_json(f"{ISS}/securities/{secid}.json?iss.meta=off&iss.only=description")
    facts = {row.get("name"): row.get("value") for row in rows_as_dicts(payload, "description")}
    name = str(facts.get("CONTRACTNAME") or "")
    return {
        "emitent_id": facts.get("EMITTER_ID"),
        "group_type": facts.get("GROUPTYPE"),
        "lot_size": facts.get("LOTSIZE"),
        "asset_code": facts.get("ASSETCODE"),
        # У эмитента бывает несколько серий на один emitent_id: обыкновенные,
        # привилегированные и «мини». Различить их можно только по названию контракта —
        # ASSETCODE (TATN против TATP) правилом не формализуется.
        "on_preferred": "привилегированн" in name.lower(),
        "is_mini": "(мини)" in name.lower() or "мини)" in name.lower(),
    }


def shares_by_emitent() -> dict[str, str]:
    """emitent_id → тикер обыкновенной акции основного режима TQBR.

    Привилегированные не берутся: фьючерс поставляется обыкновенными, и привязать
    к нему ряд по префу значило бы подписать чужую бумагу.
    """
    out: dict[str, str] = {}
    start = 0
    while True:
        payload = http_json(f"{ISS}/securities.json?iss.meta=off&engine=stock&market=shares"
                            f"&start={start}")
        rows = rows_as_dicts(payload, "securities")
        if not rows:
            break
        for row in rows:
            if (row.get("type") == "common_share" and row.get("primary_boardid") == "TQBR"
                    and row.get("is_traded") and row.get("emitent_id") and row.get("secid")):
                out.setdefault(str(row["emitent_id"]), row["secid"])
        if len(rows) < 100:
            break
        start += len(rows)
        time.sleep(PAUSE)
    return out


def build_mapping() -> dict[str, dict]:
    """{тикер акции: {futoi_code, asset_code, lot_size, in_futoi}} по всем фьючерсам на акции.

    Бумаги, которых нет в FUTOI, здесь НЕ отбрасываются, а помечаются in_futoi=False.
    Пример, ради которого это сделано: у БСП торгуются BSU6 и BSZ6, но кода BS в FUTOI
    нет вовсе. «Фьючерс есть, а позиций клиентских групп источник не публикует» и
    «у бумаги нет фьючерса» — разные факты, и молча схлопывать их в один нельзя.
    """
    universe = futoi_universe()
    quarterly = forts_quarterly_groups()
    by_emitent = shares_by_emitent()

    candidates: dict[str, list[dict]] = collections.defaultdict(list)
    for asset_code, secid in sorted(quarterly.items()):
        code = secid[:2]                       # квартальный SECID = 2 буквы + месяц + год
        facts = contract_facts(secid)
        time.sleep(PAUSE)
        if facts.get("group_type") != "Акции":
            continue
        share = by_emitent.get(str(facts.get("emitent_id")))
        if not share:
            continue
        candidates[share].append({
            "futoi_code": code,
            "asset_code": asset_code,
            "lot_size": int(facts["lot_size"]) if str(facts.get("lot_size", "")).isdigit() else None,
            "in_futoi": code in universe,
            "on_preferred": bool(facts.get("on_preferred")),
            "is_mini": bool(facts.get("is_mini")),
        })

    # На одну обыкновенную акцию нередко приходится несколько серий: у Татнефти это TT
    # (обыкновенные) и TP (привилегированные), у Полюса — основной контракт и «мини».
    # emitent_id у них общий, поэтому без явного выбора побеждала бы последняя по алфавиту,
    # и график обыкновенной акции получил бы ряд по префам.
    mapping: dict[str, dict] = {}
    for share, options in candidates.items():
        best = sorted(options, key=lambda o: (not o["in_futoi"], o["on_preferred"], o["is_mini"],
                                              o["futoi_code"]))[0]
        mapping[share] = {k: best[k] for k in ("futoi_code", "asset_code", "lot_size", "in_futoi")}
    return mapping


# ─────────────────────────── ряд позиций ───────────────────────────


def window_end(year: int, till_cap: str) -> str:
    """Правый край окна: конец года, но не позже последней доступной даты.

    Без ограничения окно текущего года запрашивало бы 31 декабря, то есть будущее внутри
    закрытых 14 дней, и ISS отвечал бы отказом на ВЕСЬ год. Ряд тогда обрывался прошлым
    декабрём — данные выглядели бы просто устаревшими, хотя причина в запросе.
    """
    end = f"{year}-12-31"
    return min(end, till_cap)


def fetch_series(code: str, year_to: int, till_cap: str | None = None) -> list[dict]:
    """Последний срез каждого дня по коду базового актива, окнами по годам."""
    seen: dict[str, dict] = {}
    denied: list[str] = []
    cap = till_cap or f"{year_to}-12-31"
    for year in range(HISTORY_FROM_YEAR, year_to + 1):
        till = window_end(year, cap)
        if till < f"{year}-01-01":
            continue
        url = (f"{ISS}/analyticalproducts/futoi/securities/{code}.json?iss.meta=off&latest=1"
               f"&from={year}-01-01&till={till}")
        try:
            rows = rows_as_dicts(http_json(url), "futoi")
        except IssError as exc:
            # Отказ по свежему окну — норма для бесплатного доступа; по старому — повод
            # сказать об этом, а не молча укоротить историю.
            denied.append(f"{year}: {exc}")
            continue
        finally:
            time.sleep(PAUSE)
        for row in rows:
            if row.get("clgroup") != "FIZ":
                continue
            pos, long, short = row.get("pos"), row.get("pos_long"), row.get("pos_short")
            if pos is None or long is None or short is None:
                continue
            # Инвариант источника. Строка, которая его нарушает, — не «слегка неточная»:
            # значит поля означают не то, что мы думаем, и брать её нельзя.
            if pos != long + short:
                continue
            date = row.get("tradedate")
            if not date:
                continue
            seen[date] = {
                "d": date,
                "pos": int(pos),
                "long": int(long),
                "short": int(short),
                "long_num": int(row.get("pos_long_num") or 0),
                "short_num": int(row.get("pos_short_num") or 0),
                "t": row.get("tradetime"),
            }
    series = [seen[d] for d in sorted(seen)]
    if denied and not series:
        raise IssError("; ".join(denied))
    return series


def z_score(values: list[int]) -> float | None:
    """Отклонение последней точки от собственной истории, в стандартных отклонениях."""
    window = values[-Z_WINDOW:]
    if len(window) < MIN_POINTS:
        return None
    mean = sum(window) / len(window)
    var = sum((v - mean) ** 2 for v in window) / (len(window) - 1)
    sd = var ** 0.5
    if sd <= 0:
        return None
    return round((window[-1] - mean) / sd, 2)


def summarize(series: list[dict]) -> dict:
    values = [p["pos"] for p in series]
    last = series[-1]
    total = last["long"] + abs(last["short"])
    return {
        "as_of": last["d"],
        "pos": last["pos"],
        "long": last["long"],
        "short": last["short"],
        # Доля лонгов ВНУТРИ позиций физлиц: знаменатель здесь известен точно, в отличие
        # от открытого интереса по всем участникам, поэтому её публиковать честно.
        "long_share": round(last["long"] / total, 4) if total else None,
        "holders_long": last["long_num"],
        "holders_short": last["short_num"],
        "z": z_score(values),
        "min": min(values),
        "max": max(values),
        "points": len(series),
    }


def build() -> dict:
    now = datetime.now(timezone.utc)
    token = os.getenv("MOEX_TOKEN") or ""
    # Запас в один день сверх объявленных 14: граница отсечки у источника включительная,
    # и запрос ровно на «сегодня − 14» уже получает отказ.
    lag_days = 0 if token else DELAY_DAYS + 1
    till_cap = (now - timedelta(days=lag_days)).strftime("%Y-%m-%d")
    mapping = build_mapping()

    tickers: dict[str, dict] = {}
    for share, info in sorted(mapping.items()):
        entry = {"futoi_code": info["futoi_code"], "asset_code": info["asset_code"],
                 "lot_size": info["lot_size"]}
        if not info.get("in_futoi"):
            # Машинный код, а не готовая фраза: формулировку выбирает интерфейс, иначе
            # он либо повторяет её дважды, либо расходится с ней по смыслу.
            entry.update(status="futoi_unavailable", reason_code="not_in_futoi")
            tickers[share] = entry
            continue
        try:
            series = fetch_series(info["futoi_code"], now.year, till_cap)
        except IssError as exc:
            # Фьючерс торгуется, а строк в FUTOI нет — это отдельное состояние, и путать
            # его с «у бумаги нет фьючерса» нельзя: вывод для инвестора разный.
            entry.update(status="futoi_unavailable", reason_code="source_error",
                         reason=str(exc)[:200])
            tickers[share] = entry
            continue
        if len(series) < MIN_POINTS:
            entry.update(status="futoi_unavailable", reason_code="short_series",
                         reason=f"точек в ряду {len(series)} — меньше минимума {MIN_POINTS}")
            tickers[share] = entry
            continue
        # В ряд идут только дата и нетто-позиция: long/short по каждому дню утроили бы
        # файл, а читаются лишь для последней точки — она целиком лежит в summary.
        entry.update(status="ok",
                     dates=[p["d"] for p in series],
                     pos=[p["pos"] for p in series],
                     summary=summarize(series))
        tickers[share] = entry

    ok = [t for t in tickers.values() if t.get("status") == "ok"]
    as_of = max((t["summary"]["as_of"] for t in ok), default=None)
    return {
        "meta": {
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "MOEX ISS, analyticalproducts/futoi",
            "client_group": "FIZ — физические лица",
            "as_of": as_of,
            "delayed": not token,
            "delay_days": 0 if token else DELAY_DAYS,
            "contracts_scope": "квартальные фьючерсы (агрегат всех экспираций базового актива)",
            "excludes_perpetual": True,
            "scope_note": (
                "Вечные фьючерсы не включены: они появились в источнике между июнем 2024 "
                "и июнем 2025, и сумма с квартальными дала бы скачок ряда в этой точке, "
                "неотличимый от всплеска интереса физлиц."
            ),
            "unit": "контракты (нетто-позиция физлиц)",
            "no_oi_share": (
                "Доля от открытого интереса не публикуется: семантика знаменателя в "
                "источнике не подтверждена."
            ),
            "tickers_ok": len(ok),
            "tickers_total": len(tickers),
        },
        "tickers": tickers,
    }


def main() -> int:
    try:
        payload = build()
    except IssError as exc:
        print(f"[futoi] источник недоступен: {exc}", file=sys.stderr)
        return 1
    if not payload["meta"]["tickers_ok"]:
        print("[futoi] ни одного ряда — файл не перезаписываем", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    meta = payload["meta"]
    print(f"[futoi] {meta['tickers_ok']} из {meta['tickers_total']} рядов, "
          f"последний день {meta['as_of']}, лаг {meta['delay_days']} дн. → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
