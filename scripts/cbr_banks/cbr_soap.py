#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SOAP-клиент к веб-сервису ЦБ РФ по кредитным организациям (CreditOrgInfo.asmx).
Только stdlib (urllib) + retry/backoff. Никакой синтетики — все значения приходят от ЦБ.

Проверено на реальном сервисе (2026-07): 125 операций; путь получения Ф.102 —
  RegNumToIntCode(RegNumber=<рег.№>)  → внутренний код (для справки),
  Data102F(CredorgNumber=<рег.№>, dt=<квартал>) → форма 102: строки {symbol, symname, tp3},
     значение tp3 — тыс. руб., накопленным итогом с начала года; шапка содержит cname/cregnr.

ГОЧИ (выстрадано пробами): GetFormsMaxDate(code:int) даёт 500 на 13-значном внутр. коде (int32
overflow) — НЕ используем; доступные даты определяем перебором квартальных дат и проверкой ответа.
Form102IndicatorsEnum отдаёт ~22 МБ — НЕ тянем, имена показателей уже есть в Data102F (symname).
"""
from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

BASE = "http://www.cbr.ru/CreditInfoWebServ/CreditOrgInfo.asmx"
NS = "http://web.cbr.ru/"
UA = {"User-Agent": "dividend-site/cbr-banks (+github.com/eremkindv91)"}


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def soap_post(action: str, inner_xml: str, retries: int = 6, timeout: int = 90) -> str:
    env = ('<?xml version="1.0" encoding="utf-8"?>'
           '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
           f"<soap:Body>{inner_xml}</soap:Body></soap:Envelope>")
    headers = {**UA, "Content-Type": "text/xml; charset=utf-8", "SOAPAction": NS + action}
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(BASE, data=env.encode("utf-8"), headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            sys.stderr.write(f"[cbr_soap] {action}: попытка {a + 1}/{retries} не удалась: {str(e)[:120]}\n")
            time.sleep(2 ** a)
    raise RuntimeError(f"CBR SOAP недоступен ({action}): {last}")


def reg_to_intcode(reg_num: int) -> int | None:
    """Внутренний код КО по регистрационному номеру (для справки/CreditInfo)."""
    r = soap_post("RegNumToIntCode", f'<RegNumToIntCode xmlns="{NS}"><RegNumber>{reg_num}</RegNumber></RegNumToIntCode>')
    try:
        val = ET.fromstring(r.encode("utf-8")).find(".//{*}RegNumToIntCodeResult").text
        code = int(val)
        return code if code > 0 else None
    except (AttributeError, ValueError, TypeError):
        return None


def data102f(reg_num: int, dt: str) -> dict | None:
    """
    Форма 102 банка на отчётную дату dt (YYYY-MM-DD, квартальная).
    Вернуть {reg_num, name, dt, rows:[{symbol, symname, value(float, тыс.руб.)}]} или None, если данных нет.
    """
    r = soap_post("Data102F", f'<Data102F xmlns="{NS}"><CredorgNumber>{reg_num}</CredorgNumber><dt>{dt}</dt></Data102F>')
    root = ET.fromstring(r.encode("utf-8"))
    name = None
    for tag in ("cname", "cnamer", "cnamep"):
        el = root.find(f".//{{*}}{tag}")
        if el is not None and el.text:
            name = el.text.strip(); break
    rows = []
    for row in root.iter():
        if _local(row.tag) != "F102":
            continue
        d = {_local(c.tag): (c.text or "").strip() for c in row}
        sym, val = d.get("symbol"), d.get("tp3")
        if not sym:
            continue
        try:
            v = float(val) if val not in (None, "") else None
        except ValueError:
            v = None
        rows.append({"symbol": sym, "symname": d.get("symname", ""), "value": v})
    if not rows:
        return None
    return {"reg_num": reg_num, "name": name, "dt": dt, "rows": rows}


def get_dates_for_form(form: str, reg_num: int) -> list[str]:
    """Реальные доступные отчётные даты формы ('101'/'102'/'123'/'134'/'135') по банку (GetDatesForFxxx).
    ГОЧА: имя параметра в WSDL — CredprgNumber (опечатка ЦБ, не CredorgNumber)."""
    action = f"GetDatesForF{form}"
    r = soap_post(action, f'<{action} xmlns="{NS}"><CredprgNumber>{reg_num}</CredprgNumber></{action}>')
    return sorted({m.split("T")[0] for m in
                   (el.text for el in ET.fromstring(r.encode("utf-8")).iter() if _local(el.tag) == "dateTime")
                   if m})


def data123(reg_num: int, dt: str) -> dict | None:
    """Форма 123 (капитал, Базель III) на дату. Вернуть {reg_num, dt, rows:[{code, name, value}]} или None.
    Значения тыс. руб. Проверено: код 000=капитал итого, 102=базовый, 203=дополнительный."""
    r = soap_post("Data123FormFull",
                  f'<Data123FormFull xmlns="{NS}"><CredorgNumber>{reg_num}</CredorgNumber><OnDate>{dt}</OnDate></Data123FormFull>')
    rows = []
    for row in ET.fromstring(r.encode("utf-8")).iter():
        if _local(row.tag) != "F123":
            continue
        d = {_local(c.tag): (c.text or "").strip() for c in row}
        code, val = d.get("CODE"), d.get("VALUE")
        if not code:
            continue
        try:
            v = float(val) if val not in (None, "") else None
        except ValueError:
            v = None
        rows.append({"code": code, "name": d.get("NAME", ""), "value": v})
    return {"reg_num": reg_num, "dt": dt, "rows": rows} if rows else None


def data135(reg_num: int, dt: str) -> dict | None:
    """Форма 135, раздел 3 (обязательные нормативы) на дату. Вернуть {reg_num, dt, rows:[{code, value(%)}]}.
    Проверено: строки F135_3 с C3 (Н1.0/Н1.1/Н1.2/Н2/Н3/Н4...) и V3 (значение в %)."""
    r = soap_post("Data135FormFull",
                  f'<Data135FormFull xmlns="{NS}"><CredorgNumber>{reg_num}</CredorgNumber><OnDate>{dt}</OnDate></Data135FormFull>')
    rows = []
    for row in ET.fromstring(r.encode("utf-8")).iter():
        if _local(row.tag) != "F135_3":
            continue
        d = {_local(c.tag): (c.text or "").strip() for c in row}
        code, val = d.get("C3"), d.get("V3")
        if not code:
            continue
        try:
            v = float(val) if val not in (None, "") else None
        except ValueError:
            v = None
        rows.append({"code": code, "value": v})
    return {"reg_num": reg_num, "dt": dt, "rows": rows} if rows else None


if __name__ == "__main__":
    rn = int(sys.argv[1]) if len(sys.argv) > 1 else 1481
    dt = sys.argv[2] if len(sys.argv) > 2 else "2026-04-01"
    print("IntCode:", reg_to_intcode(rn))
    f = data102f(rn, dt)
    if f:
        print(f"{f['name']} рег.{rn} на {dt}: строк {len(f['rows'])}")
        for row in f["rows"]:
            if row["symbol"] in ("01000", "61101") and row["value"] is not None:
                print(f"  [{row['symbol']}] {row['symname'][:50]} = {row['value']:,.0f} тыс.₽")
