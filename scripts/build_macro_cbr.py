#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Макро-слой от Банка России: ключевая ставка (дневная история) + инфляция г/г (месячная).

Зачем частному инвестору именно это:
  • КЛЮЧЕВАЯ СТАВКА — одновременно ставка дисконтирования и альтернатива в виде вклада.
    Пока она 14%, дивидендная доходность 8% проигрывает депозиту, и это главный факт
    при выборе между акциями и вкладом;
  • ИНФЛЯЦИЯ г/г — переводит номинальную доходность в реальную. Дивиденды 10% при
    инфляции 6% — это +4% покупательной способности, а не +10%;
  • РЕАЛЬНАЯ СТАВКА (ставка − инфляция) — насколько жёсткая политика ЦБ сейчас;
  • ЦЕЛЬ ПО ИНФЛЯЦИИ — ориентир, к которому ЦБ ведёт ставку.

Источники (оба официальные, Банк России):
  1. SOAP DailyInfoWebServ, метод KeyRate — дневная история ключевой ставки;
  2. https://www.cbr.ru/hd_base/infl/ — таблица «Инфляция и ключевая ставка» (месяц,
     ставка, инфляция г/г, цель). Инфляции в SOAP-сервисе ЦБ НЕТ (проверено по WSDL:
     есть KeyRate/Ruonia/MKR/Bliquidity и прочие ставки, инфляции среди методов нет),
     поэтому берётся страница статистики. Формат запроса: UniDbQuery.From/To в ДД.ММ.ГГГГ.

Честность:
  • при недоступности источника прошлый файл НЕ перезаписывается (last-good остаётся);
  • данные проходят гейты правдоподобия — мусор не публикуется даже если распарсился;
  • ничего не достраивается и не интерполируется: пропущенный месяц остаётся пропуском.

Запуск:  python scripts/build_macro_cbr.py
Выход:   site/macro_cbr.json
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "site", "macro_cbr.json")
MSK = timezone(timedelta(hours=3))

SOAP_URL = "http://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx"
SOAP_NS = "http://web.cbr.ru/"
INFL_URL = "https://www.cbr.ru/hd_base/infl/"
UA = {"User-Agent": "Mozilla/5.0 (compatible; dividend-factor-strategies/1.0)"}

HISTORY_FROM = "01.01.2015"          # 12 лет — хватает, чтобы увидеть все режимы ДКП
MIN_INFL_ROWS = 60                   # меньше 5 лет — не публикуем
MIN_RATE_ROWS = 30
RATE_RANGE = (0.0, 30.0)             # ключевая ставка вне этого коридора — ошибка парсинга
INFL_RANGE = (-5.0, 30.0)


def fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    sys.stderr.write(f"[macro] ОШИБКА: {msg}\n")
    sys.exit(1)


def http(url: str, data: bytes | None = None, headers: dict | None = None,
         retries: int = 4, timeout: int = 45) -> bytes:
    """GET/POST с backoff. ЦБ периодически отвечает медленно — повторяем, а не падаем."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                pause = 2 ** (attempt - 1)
                sys.stderr.write(f"[macro] {url[:60]} не ответил ({e}); повтор через {pause}s\n")
                time.sleep(pause)
    raise RuntimeError(f"{url}: {last}")


def fetch_key_rate(days: int = 900) -> list[dict]:
    """Дневная история ключевой ставки через SOAP KeyRate."""
    today = datetime.now(MSK).date()
    frm = today - timedelta(days=days)
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        f'<KeyRate xmlns="{SOAP_NS}"><fromDate>{frm.isoformat()}</fromDate>'
        f'<ToDate>{today.isoformat()}</ToDate></KeyRate>'
        '</soap:Body></soap:Envelope>'
    ).encode("utf-8")
    xml = http(SOAP_URL, data=body, headers={
        "Content-Type": "text/xml; charset=utf-8", "SOAPAction": SOAP_NS + "KeyRate"}).decode("utf-8", "replace")
    rows = []
    for dt, rate in re.findall(r"<DT>([^<]+)</DT>\s*<Rate>([^<]+)</Rate>", xml):
        try:
            value = float(rate.replace(",", "."))
        except ValueError:
            continue
        if not (RATE_RANGE[0] <= value <= RATE_RANGE[1]):
            continue
        rows.append({"date": dt[:10], "rate": round(value, 4)})
    rows.sort(key=lambda r: r["date"])
    return rows


def fetch_inflation() -> list[dict]:
    """Месячная таблица ЦБ: ставка на конец месяца, инфляция г/г, цель."""
    today = datetime.now(MSK).date().strftime("%d.%m.%Y")
    url = f"{INFL_URL}?UniDbQuery.Posted=True&UniDbQuery.From={HISTORY_FROM}&UniDbQuery.To={today}"
    html = http(url, timeout=60).decode("utf-8", "replace")

    def num(text: str) -> float | None:
        cleaned = re.sub(r"[^\d,.\-]", "", text).replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None

    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>|&nbsp;|\s+", " ", c).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 3 or not re.match(r"^\d{2}\.\d{4}$", cells[0]):
            continue
        mm, yyyy = cells[0].split(".")
        rate, infl = num(cells[1]), num(cells[2])
        target = num(cells[3]) if len(cells) > 3 else None
        if infl is None or not (INFL_RANGE[0] <= infl <= INFL_RANGE[1]):
            continue
        if rate is not None and not (RATE_RANGE[0] <= rate <= RATE_RANGE[1]):
            rate = None
        out.append({"month": f"{yyyy}-{mm}", "year": int(yyyy), "m": int(mm),
                    "key_rate": rate, "inflation_yoy": infl, "target": target})
    out.sort(key=lambda r: r["month"])
    return out


def main() -> int:
    generated = datetime.now(MSK).replace(microsecond=0)
    try:
        rates = fetch_key_rate()
        infl = fetch_inflation()
    except Exception as e:  # noqa: BLE001
        # last-good остаётся на месте: пустой/битый макро-блок хуже вчерашнего верного
        sys.stderr.write(f"[macro] источник ЦБ недоступен, {OUT} не перезаписан: {e}\n")
        return 1

    if len(rates) < MIN_RATE_ROWS:
        fail(f"ключевая ставка: получено {len(rates)} записей, нужно ≥{MIN_RATE_ROWS}")
    if len(infl) < MIN_INFL_ROWS:
        fail(f"инфляция: получено {len(infl)} месяцев, нужно ≥{MIN_INFL_ROWS}")

    current = rates[-1]
    # предыдущее ОТЛИЧНОЕ значение ставки — чтобы показать факт и дату решения ЦБ
    prev_rate, changed_on = None, None
    for row in reversed(rates[:-1]):
        if abs(row["rate"] - current["rate"]) > 1e-9:
            prev_rate = row["rate"]
            break
    for idx in range(len(rates) - 1, 0, -1):
        if abs(rates[idx]["rate"] - rates[idx - 1]["rate"]) > 1e-9:
            changed_on = rates[idx]["date"]
            break

    last_infl = infl[-1]
    real_rate = (round(current["rate"] - last_infl["inflation_yoy"], 2)
                 if last_infl["inflation_yoy"] is not None else None)

    # свод по годам: декабрьское значение = итог года (для 2026 — последний доступный месяц)
    by_year = {}
    for row in infl:
        by_year[row["year"]] = row
    year_summary = [{"year": y, "month": by_year[y]["month"],
                     "inflation_yoy": by_year[y]["inflation_yoy"],
                     "key_rate": by_year[y]["key_rate"],
                     "partial": by_year[y]["m"] != 12}
                    for y in sorted(by_year)]

    payload = {
        "schema_version": 1,
        "generated_at": generated.isoformat(),
        "source": {
            "key_rate": {"name": "Банк России, SOAP DailyInfoWebServ/KeyRate", "url": SOAP_URL},
            "inflation": {"name": "Банк России, «Инфляция и ключевая ставка»", "url": INFL_URL},
        },
        "key_rate": {
            "current": current["rate"],
            "asof": current["date"],
            "previous": prev_rate,
            "change": round(current["rate"] - prev_rate, 4) if prev_rate is not None else None,
            "changed_on": changed_on,
            "series": rates,
        },
        "inflation": {
            "latest_yoy": last_infl["inflation_yoy"],
            "latest_month": last_infl["month"],
            "target": last_infl["target"],
            "above_target": (round(last_infl["inflation_yoy"] - last_infl["target"], 2)
                             if last_infl["target"] is not None else None),
            "monthly": infl,
            "by_year": year_summary,
        },
        "real_key_rate": real_rate,
        "note": ("Ключевая ставка — дневная история Банка России. Инфляция — % год к году из "
                 "официальной таблицы ЦБ «Инфляция и ключевая ставка»; месячная инфляция ЦБ "
                 "публикуется с лагом, поэтому последний месяц может отставать от текущей даты. "
                 "Реальная ставка = ключевая ставка − инфляция г/г. Ничего не интерполируется."),
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"[macro] записано: site/macro_cbr.json · ставка {current['rate']}% на {current['date']}"
          f" (пред. {prev_rate}, решение {changed_on}) · инфляция {last_infl['inflation_yoy']}% "
          f"г/г за {last_infl['month']} · реальная {real_rate} п.п. · месяцев {len(infl)}, лет {len(year_summary)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
