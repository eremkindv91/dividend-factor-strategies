#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка данных вкладки «Банки РФ» из веб-сервиса ЦБ РФ (Ф.102, чистая прибыль и др.). Только stdlib.
Реальные значения ЦБ, без синтетики: нет данных → статус missing, значение не выводится.

Выход в site/cbr/: banks.json, bank_timeseries.json, metadata.json, data_quality.json.

SEED — точки входа по рег.номерам (публичные стабильные факты, как тикеры); ВСЁ отображаемое
(название, значения, наличие отчётности) берётся от ЦБ. Полный универсум из списка КО ЦБ — стадия 2.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cbr_soap import BASE, data102f, reg_to_intcode  # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(REPO, "site", "cbr")
MAPPING = os.path.join(HERE, "metric_mapping.json")
REGISTRY_URL = "https://www.cbr.ru/banking_sector/credit/FullCoList/"
SIB_URL = "https://www.cbr.ru/banking_sector/credit/SystemBanks.html/"

# рег.№ + системная значимость (список СЗКО ЦБ). Названия и данные — из ответа ЦБ.
SEED = [
    (1481, True), (1000, True), (354, True), (1326, True), (963, True), (2673, True),
    (1978, True), (3349, True), (3251, True), (3292, True), (1, True), (2312, True),
]


def quarters(back_years: int = 2) -> list[str]:
    """Квартальные отчётные даты (01-01/04/07/10) за последние back_years+ до текущего квартала."""
    today = date.today()
    out = []
    for y in range(today.year - back_years, today.year + 1):
        for m in (1, 4, 7, 10):
            d = date(y, m, 1)
            if d <= today:
                out.append(d.isoformat())
    return out


def load_mapping() -> dict:
    with open(MAPPING, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    mapping = load_mapping()
    metrics = mapping["metrics"]
    sym2metric = {m["symbol"]: m for m in metrics}
    dates = quarters(2)
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # задачи: (reg_num, dt) — параллельно тянем Ф.102
    tasks = [(rn, dt) for rn, _sib in SEED for dt in dates]

    def fetch(t):
        rn, dt = t
        try:
            return (rn, dt, data102f(rn, dt))
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[cbr] Data102F({rn},{dt}) ошибка: {str(e)[:100]}\n")
            return (rn, dt, "error")

    results: dict[tuple, object] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        for rn, dt, res in ex.map(fetch, tasks):
            results[(rn, dt)] = res

    banks, timeseries, dq_banks = [], {}, []
    values_loaded = missing = errors = 0
    all_report_dates = set()
    intcodes = {}
    # int_code по банкам (для справки), параллельно
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        for rn, ic in zip([s[0] for s in SEED], ex.map(lambda rn: reg_to_intcode(rn), [s[0] for s in SEED])):
            intcodes[rn] = ic

    for reg_num, is_sib in SEED:
        name = None
        ts: dict[str, list] = {m["metric_id"]: [] for m in metrics}
        bank_missing = 0
        has_any = False
        for dt in dates:
            res = results.get((reg_num, dt))
            if res == "error":
                errors += 1
                continue
            if not res:                     # нет данных на дату — норм (отчётность не за все даты)
                continue
            has_any = True
            name = name or res.get("name")
            by_sym = {r["symbol"]: r for r in res["rows"]}
            for m in metrics:
                row = by_sym.get(m["symbol"])
                if row and row["value"] is not None:
                    ts[m["metric_id"]].append({
                        "date": dt, "value": row["value"], "symbol": m["symbol"],
                        "form": m["form"], "unit": m["unit"],
                        "quality_status": m["reliability_level"],
                        "source": mapping["source"],
                    })
                    values_loaded += 1
                    all_report_dates.add(dt)
                else:
                    bank_missing += 1
                    missing += 1
        # чистим пустые метрики
        ts = {k: v for k, v in ts.items() if v}
        timeseries[str(reg_num)] = ts
        status = "ok" if has_any else "missing"
        banks.append({
            "reg_num": reg_num, "int_code": intcodes.get(reg_num),
            "name": name or f"рег.№ {reg_num}",
            "is_systemically_important": is_sib,
            "is_active": has_any,
            "license_status": "active" if has_any else "unknown",
            "source_url": BASE, "last_checked_at": checked_at,
        })
        dq_banks.append({"reg_num": reg_num, "name": name or f"рег.№ {reg_num}",
                         "status": status, "metrics_with_data": list(ts.keys()),
                         "missing_points": bank_missing})

    last_report_date = max(all_report_dates) if all_report_dates else None
    banks.sort(key=lambda b: (not b["is_active"], b["name"]))

    metadata = {
        "generated_at": checked_at, "last_checked_at": checked_at,
        "source": "Банк России, веб-сервис CreditOrgInfo (форма 102)",
        "source_url": BASE, "registry_url": REGISTRY_URL, "sib_url": SIB_URL,
        "forms": ["102"], "last_report_date": last_report_date,
        "banks_count": len(banks), "dates_probed": dates,
        "unit": mapping["unit"], "value_field": mapping["value_field"], "cumulative": mapping["cumulative"],
        "note": "Ф.102 (отчёт о фин. результатах), значения в тыс. руб. накопленным итогом с начала года. "
                "Реестр — точечный (12 системно значимых банков) по рег.номерам; данные из ЦБ. "
                "Отчётность ЦБ не ежедневная (квартальная). Не ИИР.",
    }
    data_quality = {
        "generated_at": checked_at,
        "banks_total": len(banks),
        "banks_active": sum(1 for b in banks if b["is_active"]),
        "banks_sib": sum(1 for b in banks if b["is_systemically_important"]),
        "values_loaded": values_loaded, "missing": missing, "errors": errors,
        "last_report_date": last_report_date,
        "metrics": [{"metric_id": m["metric_id"], "name": m["display_name_ru"],
                     "symbol": m["symbol"], "reliability": m["reliability_level"]} for m in metrics],
        "banks": dq_banks,
        "statuses_legend": ["ok", "missing", "stale", "parse_error", "source_unavailable",
                            "calculated", "official_direct"],
    }

    if values_loaded == 0:
        sys.stderr.write("[cbr] СТОП: не загружено ни одного значения — не публикуем (источник недоступен?)\n")
        return 1

    payloads = {"banks.json": banks, "bank_timeseries.json": timeseries,
                "metadata.json": metadata, "data_quality.json": data_quality,
                "metric_mapping.json": mapping}
    for fname, obj in payloads.items():
        with open(os.path.join(OUT, fname), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    sys.stderr.write(f"[cbr] OK → {OUT}: банков {len(banks)} (актив {metadata['banks_count']}), "
                     f"значений {values_loaded}, последняя дата {last_report_date}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
