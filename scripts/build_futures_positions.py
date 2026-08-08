#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Открытые позиции физических и юридических лиц во фьючерсах (MOEX ISS, openpositions).

Заменяет сбор через analyticalproducts/futoi. Данные те же самые — сверено на 23.07.2026
по SBRF: long 121 766, short 126 389 в обоих источниках до единицы, — но этот эндпоинт
отдаёт их за ПРЕДЫДУЩИЙ ТОРГОВЫЙ ДЕНЬ, а не с отсечкой в две недели, и историю с 2012
года вместо 2020. Разница не косметическая: по Сберу на 23.07 физлица были в нетто-шорте
−4 623 контракта, а на 06.08 — в нетто-лонге +47 525. Двухнедельный лаг скрывал разворот.

Только stdlib: workflow работает без pip install.

ЧТО ОТДАЁТ ИСТОЧНИК (проверено на живом API)
    /iss/statistics/engines/futures/markets/forts/openpositions/{asset}.json
    tradedate · asset · is_fiz · persons_long · persons_short
    open_position_long · open_position_short · oichange_long · oichange_short
  • is_fiz: 1 — физические лица, 0 — юридические. Ряды зеркальны по нетто-позиции,
    поэтому публикуется сторона физлиц, а юрлица идут справочно.
  • Вся история приходит ОДНИМ запросом (6 900 строк по MIX за 14 лет) — пагинация
    и разбиение по годам, нужные старому источнику, здесь не требуются.
  • asset — код базового актива фьючерса (SBRF, GAZR, MIX), а не тикер акции.

ЕДИНИЦЫ. Источник считает в КОНТРАКТАХ, и ряд публикуется в них же. Рублёвый эквивалент
считается только там, где для него есть все три множителя:
    Notional = Контракты × Цена × (STEPPRICE / MINSTEP)
Для индексных контрактов это даёт 1 ₽ за пункт у MIX и 10 ₽ у MXI и IMOEX — мини-контракт
и вечный ровно вдесятеро мельче основного, что и подтверждает расчёт. Без проверенной
спецификации никакие «млрд ₽» не публикуются: перевод контрактов в рубли на глаз —
самый простой способ ошибиться на порядок.

СЛОЖЕНИЕ КОНТРАКТОВ РАЗНЫХ СЕРИЙ ЗАПРЕЩЕНО. MIX, MXI и IMOEX относятся к одному индексу,
но контракт у них разного размера: «86 086 + 160 755 + 804 535 контрактов» — величина без
экономического смысла. Складывать можно только рублёвые notional, и только после проверки
спецификации каждого.

РОЛЛОВЕР не нужен: источник агрегирует позиции по базовому активу, а не по серии. Для
рублёвого ряда по индексу берётся ВЕЧНЫЙ контракт IMOEXF — у него нет экспираций, поэтому
цена образует непрерывный ряд без склейки и без искусственных разрывов на переходах.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "futures_positions.json"

ISS = "https://iss.moex.com/iss"
UA = "dividend-factor-strategies/positions (+https://github.com/eremkindv91/dividend-factor-strategies)"
HISTORY_FROM = "2012-01-01"
PAUSE = 0.12
MIN_POINTS = 30
Z_WINDOW = 250
# В файл уходят последние три года: график дальше всё равно не показывает, а перцентиль
# и z-score считаются ДО обрезки — по всей доступной истории с 2012 года.
HISTORY_KEEP = 800

# Индексные контракты: код актива → (SECID для цены, человеческое имя, комментарий).
# IMOEX стоит первым: вечный контракт даёт непрерывную цену без ролловера, поэтому
# именно он несёт рублёвый ряд.
INDEX_ASSETS = [
    ("IMOEX", "IMOEXF", "Вечный фьючерс на Индекс МосБиржи"),
    ("MIX", "MXU6", "Фьючерс на Индекс МосБиржи"),
    ("MXI", "MMU6", "Фьючерс на Индекс МосБиржи (мини)"),
]


class IssError(RuntimeError):
    """Источник не отдал данные."""


def http_json(url: str, tries: int = 3) -> dict:
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:
                # ISS отдаёт часть ответов с BOM — json.load на нём падает.
                return json.loads(resp.read().decode("utf-8-sig"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            last = exc
            time.sleep(0.6 * (attempt + 1))
    raise IssError(f"{url}: {last}")


def rows_of(payload: dict, block: str) -> list[dict]:
    b = payload.get(block) or {}
    cols = b.get("columns") or []
    if "ERROR_MESSAGE" in cols:
        data = b.get("data") or []
        raise IssError(str(data[0][cols.index("ERROR_MESSAGE")]) if data else "отказ источника")
    return [dict(zip(cols, row)) for row in (b.get("data") or [])]


# ─────────────────────────── позиции ───────────────────────────


def positions(asset: str, date_to: str) -> list[dict]:
    """Ряд позиций ФИЗЛИЦ по базовому активу: контракты и число участников."""
    payload = http_json(f"{ISS}/statistics/engines/futures/markets/forts/openpositions/"
                        f"{asset}.json?iss.meta=off&from={HISTORY_FROM}&till={date_to}")
    out: dict[str, dict] = {}
    for row in rows_of(payload, "open_positions"):
        if row.get("is_fiz") != 1:
            continue
        long, short = row.get("open_position_long"), row.get("open_position_short")
        date = row.get("tradedate")
        if date is None or long is None or short is None:
            continue
        out[date] = {
            "d": date, "long": int(long), "short": int(short), "net": int(long) - int(short),
            "gross": int(long) + int(short),
            "persons_long": int(row.get("persons_long") or 0),
            "persons_short": int(row.get("persons_short") or 0),
        }
    return [out[d] for d in sorted(out)]


def contract_multiplier(secid: str) -> tuple[float | None, dict]:
    """Рублей за пункт цены: STEPPRICE / MINSTEP из спецификации контракта."""
    payload = http_json(f"{ISS}/engines/futures/markets/forts/securities/{secid}.json"
                        "?iss.meta=off&iss.only=securities")
    rows = rows_of(payload, "securities")
    if not rows:
        return None, {}
    spec = rows[0]
    step, price_step = spec.get("MINSTEP"), spec.get("STEPPRICE")
    if not step or not price_step:
        return None, spec
    return float(price_step) / float(step), spec


def price_history(secid: str, date_to: str) -> dict[str, float]:
    """Дневные цены закрытия фьючерса (пагинация по 100)."""
    out: dict[str, float] = {}
    start = 0
    for _ in range(200):
        payload = http_json(f"{ISS}/history/engines/futures/markets/forts/securities/{secid}.json"
                            f"?iss.meta=off&iss.only=history&history.columns=TRADEDATE,CLOSE,SETTLEPRICE"
                            f"&from={HISTORY_FROM}&till={date_to}&start={start}")
        rows = rows_of(payload, "history")
        if not rows:
            break
        for row in rows:
            value = row.get("CLOSE") if row.get("CLOSE") else row.get("SETTLEPRICE")
            if row.get("TRADEDATE") and value:
                out[row["TRADEDATE"]] = float(value)
        if len(rows) < 100:
            break
        start += len(rows)
        time.sleep(PAUSE)
    return out


# ─────────────────────────── статистика ───────────────────────────


def z_score(values: list[float]) -> float | None:
    window = values[-Z_WINDOW:]
    if len(window) < MIN_POINTS:
        return None
    mean = sum(window) / len(window)
    var = sum((v - mean) ** 2 for v in window) / (len(window) - 1)
    sd = var ** 0.5
    return round((window[-1] - mean) / sd, 2) if sd > 0 else None


def percentile(values: list[float]) -> float | None:
    """Место последней точки среди наблюдений за год, 0–100."""
    window = values[-Z_WINDOW:]
    if len(window) < MIN_POINTS:
        return None
    last = window[-1]
    below = sum(1 for v in window if v < last)
    return round(100 * below / (len(window) - 1), 1)


def value_of(row: dict, key: str) -> int:
    """Значение поля; gross и net выводятся из сторон, а не требуются в строке."""
    if key == "gross":
        return row["long"] + row["short"]
    if key == "net":
        return row.get("net", row["long"] - row["short"])
    return row[key]


def change_over(rows: list[dict], days: int, key: str = "net") -> int | None:
    """Изменение за N наблюдений — по фактическому ряду, без домысливания пропусков."""
    if len(rows) <= days:
        return None
    return value_of(rows[-1], key) - value_of(rows[-1 - days], key)


def robust_z(values: list[float]) -> float | None:
    """z по медиане и MAD: (x − median) / (1.4826 · MAD).

    Обычный z-score здесь врёт. Ряд изменений позиций содержит редкие выбросы —
    экспирации, всплески активности, — и они раздувают стандартное отклонение так,
    что настоящее движение выглядит рядовым. Медиана и MAD к выбросам устойчивы,
    множитель 1.4826 приводит MAD к масштабу сигмы нормального распределения.
    """
    window = [v for v in values[-Z_WINDOW:] if v is not None]
    if len(window) < MIN_POINTS:
        return None
    ordered = sorted(window)

    def median(seq: list[float]) -> float:
        mid = len(seq) // 2
        return seq[mid] if len(seq) % 2 else (seq[mid - 1] + seq[mid]) / 2

    med = median(ordered)
    mad = median(sorted(abs(v - med) for v in window))
    if mad <= 0:
        return None          # ряд без разброса — «насколько это необычно» смысла не имеет
    return round((window[-1] - med) / (1.4826 * mad), 2)


def change_series(rows: list[dict], days: int, key: str) -> list[float]:
    """Ряд изменений за N наблюдений — вход для robust z."""
    return [value_of(rows[i], key) - value_of(rows[i - days], key)
            for i in range(days, len(rows))]


def summarize(rows: list[dict], multiplier: float | None, price: float | None) -> dict:
    last = rows[-1]
    nets = [float(r["net"]) for r in rows]
    gross = last["long"] + last["short"]
    out = {
        "as_of": last["d"],
        "long": last["long"], "short": last["short"], "net": last["net"],
        # Gross показывает масштаб присутствия: Net может стоять на месте, пока обе
        # стороны растут вдвое, и одно только Net этого не покажет.
        "gross": gross,
        "persons_long": last["persons_long"], "persons_short": last["persons_short"],
        # Доля лонгов внутри позиций физлиц: знаменатель известен точно, в отличие от
        # доли в общем открытом интересе рынка.
        "long_share": round(last["long"] / gross, 4) if gross else None,
        "net_ratio": round(last["net"] / gross, 4) if gross else None,
        "z": z_score(nets), "percentile": percentile(nets),
        "change_1d": change_over(rows, 1), "change_5d": change_over(rows, 5),
        "change_20d": change_over(rows, 20),
        # Декомпозиция: одно и то же изменение Net получается и набором длинных, и
        # закрытием коротких. Без сторон причина неотличима от следствия.
        "long_change_1d": change_over(rows, 1, "long"),
        "long_change_5d": change_over(rows, 5, "long"),
        "long_change_20d": change_over(rows, 20, "long"),
        "short_change_1d": change_over(rows, 1, "short"),
        "short_change_5d": change_over(rows, 5, "short"),
        "short_change_20d": change_over(rows, 20, "short"),
        "gross_change_1d": change_over(rows, 1, "gross"),
        "gross_change_5d": change_over(rows, 5, "gross"),
        "gross_change_20d": change_over(rows, 20, "gross"),
        "persons_long_change_5d": change_over(rows, 5, "persons_long"),
        "persons_short_change_5d": change_over(rows, 5, "persons_short"),
        "net_change_5d_robust_z": robust_z(change_series(rows, 5, "net")),
        "gross_change_5d_robust_z": robust_z(change_series(rows, 5, "gross")),
        "min": min(r["net"] for r in rows), "max": max(r["net"] for r in rows),
        "points": len(rows),
    }
    if multiplier and price:
        out["price"] = price
        out["multiplier"] = multiplier
        out["net_rub"] = round(last["net"] * price * multiplier, 0)
        out["long_rub"] = round(last["long"] * price * multiplier, 0)
        out["short_rub"] = round(last["short"] * price * multiplier, 0)
    return out


# ─────────────────────────── справочник акций ───────────────────────────


def shares_by_emitent() -> dict[str, str]:
    """emitent_id → тикер обыкновенной акции TQBR."""
    out: dict[str, str] = {}
    start = 0
    while True:
        rows = rows_of(http_json(f"{ISS}/securities.json?iss.meta=off&engine=stock&market=shares"
                                 f"&start={start}"), "securities")
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


def equity_assets() -> dict[str, dict]:
    """Код актива фьючерса → {ticker, secid}. Связь через emitent_id, а не по буквам кода.

    У Сбербанка ASSETCODE = SBRF, тикер акции = SBER — общего нет. На один emitent_id при
    этом приходится несколько серий: обыкновенные, привилегированные (TATN против TATP)
    и «мини». Без явного выбора график обыкновенной акции получил бы ряд по префам.
    """
    by_emitent = shares_by_emitent()
    rows = rows_of(http_json(f"{ISS}/engines/futures/markets/forts/securities.json"
                             "?iss.meta=off&iss.only=securities"), "securities")
    seen: dict[str, str] = {}
    for row in rows:
        code, secid = row.get("ASSETCODE"), row.get("SECID")
        if code and secid and str(row.get("LASTTRADEDATE")) != "2100-01-01":
            seen.setdefault(code, secid)

    out: dict[str, dict] = {}
    for code, secid in sorted(seen.items()):
        payload = http_json(f"{ISS}/securities/{secid}.json?iss.meta=off&iss.only=description")
        time.sleep(PAUSE)
        spec = {r.get("name"): r.get("value") for r in rows_of(payload, "description")}
        if spec.get("GROUPTYPE") != "Акции":
            continue
        ticker = by_emitent.get(str(spec.get("EMITTER_ID")))
        if not ticker:
            continue
        name = str(spec.get("CONTRACTNAME") or "")
        rank = (("привилегированн" in name.lower()), ("мини" in name.lower()), code)
        if ticker not in out or rank < out[ticker]["_rank"]:
            out[ticker] = {"asset": code, "secid": secid, "_rank": rank}
    for v in out.values():
        v.pop("_rank", None)
    return out


# ─────────────────────────── сборка ───────────────────────────


def build(today: datetime | None = None) -> dict:
    today = today or datetime.now(timezone.utc)
    date_to = today.strftime("%Y-%m-%d")

    indices: dict[str, dict] = {}
    for asset, secid, title in INDEX_ASSETS:
        entry = {"asset": asset, "secid": secid, "title": title}
        try:
            rows = positions(asset, date_to)
        except IssError as exc:
            entry.update(status="unavailable", reason=str(exc)[:200])
            indices[asset] = entry
            continue
        if len(rows) < MIN_POINTS:
            entry.update(status="unavailable", reason=f"точек {len(rows)} — меньше {MIN_POINTS}")
            indices[asset] = entry
            continue
        multiplier, _spec = contract_multiplier(secid)
        time.sleep(PAUSE)
        prices = price_history(secid, date_to) if multiplier else {}
        last_price = prices.get(rows[-1]["d"])
        summary = summarize(rows, multiplier, last_price)      # по ПОЛНОЙ истории
        kept = rows[-HISTORY_KEEP:]
        entry.update(status="ok",
                     dates=[r["d"] for r in kept],
                     long=[r["long"] for r in kept],
                     short=[r["short"] for r in kept],
                     net=[r["net"] for r in kept],
                     summary=summary)
        # Рублёвый ряд строится только там, где цена известна на ту же дату: подставлять
        # соседнюю значило бы считать позицию по цене другого дня.
        if multiplier and prices:
            entry["net_rub"] = [
                round(r["net"] * prices[r["d"]] * multiplier, 0) if r["d"] in prices else None
                for r in kept
            ]
            entry["multiplier"] = multiplier
        indices[asset] = entry

    tickers: dict[str, dict] = {}
    for ticker, info in sorted(equity_assets().items()):
        entry = {"asset": info["asset"], "secid": info["secid"]}
        try:
            rows = positions(info["asset"], date_to)
        except IssError as exc:
            entry.update(status="unavailable", reason=str(exc)[:200])
            tickers[ticker] = entry
            continue
        time.sleep(PAUSE)
        if len(rows) < MIN_POINTS:
            entry.update(status="unavailable",
                         reason=f"точек в ряду {len(rows)} — меньше минимума {MIN_POINTS}")
            tickers[ticker] = entry
            continue
        summary = summarize(rows, None, None)                  # по ПОЛНОЙ истории
        kept = rows[-HISTORY_KEEP:]
        # У акций в ряд идёт только нетто-позиция: long и short по последнему дню лежат
        # в summary, а помесячная разбивка утроила бы файл ради данных, которых нет
        # на графике.
        entry.update(status="ok",
                     dates=[r["d"] for r in kept],
                     net=[r["net"] for r in kept],
                     summary=summary)
        tickers[ticker] = entry

    ok_t = [t for t in tickers.values() if t.get("status") == "ok"]
    ok_i = [i for i in indices.values() if i.get("status") == "ok"]
    as_of = max((x["summary"]["as_of"] for x in ok_t + ok_i), default=None)
    return {
        "meta": {
            "generated_at": today.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "MOEX ISS, statistics/engines/futures/markets/forts/openpositions",
            "client_group": "FIZ — физические лица",
            "as_of": as_of,
            "freshness": "предыдущий торговый день",
            "unit": "контракты; рублёвый эквивалент — только там, где проверена спецификация",
            "notional_formula": "Контракты × Цена × (STEPPRICE / MINSTEP)",
            "no_cross_series_sum": (
                "Контракты MIX, MXI и IMOEX не складываются: размер контракта у них разный. "
                "Складывать можно только рублёвые величины."
            ),
            "no_oi_share": (
                "Доля в общем открытом интересе рынка не публикуется: знаменатель "
                "источник не раскрывает."
            ),
            "rollover": "не требуется — источник агрегирует позиции по базовому активу",
            "tickers_ok": len(ok_t), "tickers_total": len(tickers),
            "indices_ok": len(ok_i), "indices_total": len(indices),
        },
        "indices": indices,
        "tickers": tickers,
    }


def main() -> int:
    try:
        payload = build()
    except IssError as exc:
        print(f"[positions] источник недоступен: {exc}", file=sys.stderr)
        return 1
    meta = payload["meta"]
    if not meta["tickers_ok"] and not meta["indices_ok"]:
        print("[positions] ни одного ряда — файл не перезаписываем", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[positions] акций {meta['tickers_ok']}/{meta['tickers_total']}, "
          f"индексов {meta['indices_ok']}/{meta['indices_total']}, "
          f"данные на {meta['as_of']} → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
