#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка данных вкладки «Банки РФ» из веб-сервиса ЦБ РФ. Только stdlib. Только реальные значения ЦБ:
нет данных → точка не публикуется (никакой синтетики/интерполяции).

Формы (все коды подтверждены на реальных ответах, см. metric_mapping.json):
  Ф.102 (Data102F, квартальная)      — прибыль (накопл. итогом) + расчётный «за квартал» (разность);
  Ф.123 (Data123FormFull, месячная)  — капитал итого/базовый/дополнительный;
  Ф.135 (Data135FormFull, месячная)  — нормативы Н1.0/Н1.1/Н1.2/Н2/Н3/Н4.
Отчётные даты — РЕАЛЬНЫЕ доступные из GetDatesForF102/123/135 (не перебор вслепую).

Выход в site/cbr/: banks.json, bank_timeseries.json, metadata.json, data_quality.json, metric_mapping.json.
SEED — точки входа по рег.номерам (стабильные публичные факты); имена/значения — из ЦБ.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cbr_soap import BASE, data102f, data123, data135, get_dates_for_form, reg_to_intcode  # noqa: E402

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
N_QUARTERS = 15     # глубина Ф.102 (по реальным датам ЦБ форма месячная — ~15 мес истории)
N_MONTHS = 15       # глубина Ф.123/135 (месячные точки)
WORKERS = 3         # ЦБ отдаёт 429 при большей параллельности


def load_mapping() -> dict:
    with open(MAPPING, encoding="utf-8") as f:
        return json.load(f)


def accum_year(dt: str) -> int:
    """Год накопления Ф.102 для отчётной даты: 2026-01-01 покрывает ВЕСЬ 2025, 2026-04-01 — Q1'26."""
    y, m = int(dt[:4]), int(dt[5:7])
    return y if m > 1 else y - 1


def prev_month(dt: str) -> str:
    """Календарный месяц (YYYY-MM), к которому ОТНОСИТСЯ месячная дельта Ф.102 с отчётной датой dt.
    Отчёт на 1-е число — накопл. итог на конец ПРЕДЫДУЩЕГО месяца; поэтому дельта (отчёт_D − отчёт_D−1)
    = прибыль за месяц ПЕРЕД D. '2026-06-01' → '2026-05' (май); '2026-01-01' → '2025-12' (декабрь)."""
    y, m = int(dt[:4]), int(dt[5:7])
    y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return f"{y:04d}-{m:02d}"


def add_quarter_deltas(points: list[dict]) -> None:
    """К накопленным точкам Ф.102 добавить value_q = «за период» (разность внутри года накопления)
    и period_month — календарный месяц, к которому ОТНОСИТСЯ дельта (= отчётная дата − 1 мес).
    Дата точки остаётся отчётной (для накопл. режима), а период дельты фиксируется явно, чтобы
    фронт не реконструировал его из даты (иначе «за 01.06» ошибочно подписывается июнем, а это май)."""
    pts = sorted(points, key=lambda p: p["date"])
    prev = None
    for p in pts:
        if prev is not None and accum_year(prev["date"]) == accum_year(p["date"]):
            p["value_q"] = round(p["value"] - prev["value"], 2)
        else:
            p["value_q"] = p["value"]          # первый месяц года накопления (январь не вычитается из декабря)
        p["value_q_method"] = "calculated_from_official"
        p["period_month"] = prev_month(p["date"])   # месяц, к которому относится «за период»
        prev = p


PIPELINE_VERSION = "cbr-banks/2.1.0"   # 2.1: явный period_month у Ф.102-дельт + DQ-валидация + is_stale


def compute_is_stale(last_report: str | None, today: date) -> bool:
    """Свежесть Ф.102: ЦБ публикует данные за прошлый месяц ориентировочно к 22–25 числу. До этого
    ожидаемая последняя отчётная дата — 1-е ПРОШЛОГО месяца; с 22-го — 1-е ТЕКУЩЕГО. Если фактическая
    last_report_date раньше ожидаемой — данные устарели (не подтянулся свежий отчёт)."""
    if not last_report:
        return True
    if today.day >= 22:
        y, m = today.year, today.month
    else:
        y, m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return last_report < f"{y:04d}-{m:02d}-01"


def validate_timeseries(timeseries: dict, metrics: list) -> tuple[str, list]:
    """DQ-контракт набора (§11): по каждому банку/метрике — даты уникальны и строго возрастают,
    единицы единообразны, нет None/NaN у опубликованных точек, каждое значение связано с датой (ключ,
    не позиция), у накопленных Ф.102-метрик есть period_month и он = отчётная дата − 1 мес.
    Возвращает (status, issues). Расхождения не блокируют публикацию (guard-репорт), но помечают статус."""
    cum_ids = {m["metric_id"] for m in metrics if m.get("cumulative")}
    issues = []
    for reg, ts in timeseries.items():
        for mid, points in ts.items():
            dates = [p.get("date") for p in points]
            if len(dates) != len(set(dates)):
                issues.append(f"{reg}/{mid}: дубли отчётных дат")
            if dates != sorted(dates):
                issues.append(f"{reg}/{mid}: даты не строго возрастают")
            units = {p.get("unit") for p in points}
            if len(units) > 1:
                issues.append(f"{reg}/{mid}: разные единицы {units}")
            for p in points:
                v = p.get("value")
                if v is None or (isinstance(v, float) and v != v):
                    issues.append(f"{reg}/{mid}@{p.get('date')}: null/NaN у опубликованной точки")
                if mid in cum_ids:
                    if "period_month" not in p:
                        issues.append(f"{reg}/{mid}@{p.get('date')}: нет period_month у дельты")
                    elif p["period_month"] != prev_month(p["date"]):
                        issues.append(f"{reg}/{mid}@{p.get('date')}: period_month≠дата−1мес")
    return ("passed" if not issues else "warning"), issues


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    mapping = load_mapping()
    metrics = mapping["metrics"]
    by_form = {"102": [m for m in metrics if m["form"] == "102"],
               "123": [m for m in metrics if m["form"] == "123"],
               "135": [m for m in metrics if m["form"] == "135"]}
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    regs = [rn for rn, _ in SEED]

    # 1) реальные доступные даты по каждой форме/банку (+ intcode для справки)
    def dates_task(args):
        form, rn = args
        try:
            return (form, rn, get_dates_for_form(form, rn))
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[cbr] GetDatesForF{form}({rn}) ошибка: {str(e)[:80]}\n")
            return (form, rn, [])
    date_tasks = [(f, rn) for f in ("102", "123", "135") for rn in regs]
    avail: dict[tuple, list] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for form, rn, dts in ex.map(dates_task, date_tasks):
            avail[(form, rn)] = dts
        intcodes = dict(zip(regs, ex.map(lambda rn: reg_to_intcode(rn), regs)))

    # 2) данные форм по последним датам
    fetch_plan = []                                   # (form, rn, dt)
    for rn in regs:
        fetch_plan += [("102", rn, dt) for dt in avail.get(("102", rn), [])[-N_QUARTERS:]]
        fetch_plan += [("123", rn, dt) for dt in avail.get(("123", rn), [])[-N_MONTHS:]]
        fetch_plan += [("135", rn, dt) for dt in avail.get(("135", rn), [])[-N_MONTHS:]]

    FETCHERS = {"102": data102f, "123": data123, "135": data135}

    def fetch(args):
        form, rn, dt = args
        try:
            return (form, rn, dt, FETCHERS[form](rn, dt))
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[cbr] F{form}({rn},{dt}) ошибка: {str(e)[:80]}\n")
            return (form, rn, dt, "error")

    results: dict[tuple, object] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for form, rn, dt, res in ex.map(fetch, fetch_plan):
            results[(form, rn, dt)] = res

    # 3) сборка рядов
    banks, timeseries, dq_banks = [], {}, []
    values_loaded = missing = errors = 0
    all_report_dates: dict[str, set] = {"102": set(), "123": set(), "135": set()}
    names: dict[int, str] = {}

    def key_field(form: str) -> str:
        return "symbol" if form == "102" else "code"

    for reg_num, is_sib in SEED:
        ts: dict[str, list] = {m["metric_id"]: [] for m in metrics}
        bank_missing = 0
        has_any = False
        for form, fmetrics in by_form.items():
            for dt in avail.get((form, reg_num), [])[-(N_QUARTERS if form == "102" else N_MONTHS):]:
                res = results.get((form, reg_num, dt))
                if res == "error":
                    errors += 1
                    continue
                if not res:
                    continue
                has_any = True
                if form == "102" and res.get("name"):
                    names.setdefault(reg_num, res["name"])
                kf = key_field(form)
                by_key = {r[kf]: r for r in res["rows"]}
                for m in fmetrics:
                    row = by_key.get(m["symbol"])
                    if row and row.get("value") is not None:
                        ts[m["metric_id"]].append({
                            "date": dt, "value": row["value"], "symbol": m["symbol"],
                            "form": m["form"], "unit": m["unit"],
                            "quality_status": m["reliability_level"],
                            "source": mapping["source"],
                        })
                        values_loaded += 1
                        all_report_dates[form].add(dt)
                    else:
                        bank_missing += 1
                        missing += 1
        # квартальные дельты для накопленных метрик Ф.102
        for m in metrics:
            if m.get("cumulative") and ts.get(m["metric_id"]):
                add_quarter_deltas(ts[m["metric_id"]])
        ts = {k: sorted(v, key=lambda p: p["date"]) for k, v in ts.items() if v}
        timeseries[str(reg_num)] = ts
        banks.append({
            "reg_num": reg_num, "int_code": intcodes.get(reg_num),
            "name": names.get(reg_num) or f"рег.№ {reg_num}",
            "is_systemically_important": is_sib,
            "is_active": has_any,
            "license_status": "active" if has_any else "unknown",
            "source_url": BASE, "last_checked_at": checked_at,
        })
        dq_banks.append({"reg_num": reg_num, "name": names.get(reg_num) or f"рег.№ {reg_num}",
                         "status": "ok" if has_any else "missing",
                         "metrics_with_data": list(ts.keys()), "missing_points": bank_missing})

    banks.sort(key=lambda b: (not b["is_active"], b["name"]))
    last_dates = {f: (max(s) if s else None) for f, s in all_report_dates.items()}

    # DQ-контракт набора + свежесть (§11)
    validation_status, validation_issues = validate_timeseries(timeseries, metrics)
    is_stale = compute_is_stale(last_dates["102"], date.today())

    metadata = {
        "generated_at": checked_at, "last_checked_at": checked_at,
        "source": "Банк России, веб-сервис CreditOrgInfo (формы 102/123/135)",
        "source_url": BASE, "registry_url": REGISTRY_URL, "sib_url": SIB_URL,
        "forms": ["102", "123", "135"],
        "last_report_date": last_dates["102"],
        "last_report_dates": last_dates,
        # ЦБ SOAP отдаёт только отчётные даты периодов, не дату публикации файла →
        # latest_publication_date фиксируем как момент нашего фетча (когда данные стали доступны нам).
        "latest_report_date": last_dates["102"],
        "latest_publication_date": checked_at,
        "is_stale": is_stale,
        "pipeline_version": PIPELINE_VERSION,
        "validation_status": validation_status,
        "banks_count": len(banks),
        "unit": mapping["unit"], "cumulative": mapping["cumulative"],
        "note": "Ф.102 — месячная, тыс. руб. накопленным итогом с начала года. «За период» — расчётная "
                "разность накопленных, ОТНОСИТСЯ к месяцу перед отчётной датой (отчёт на 01.06 = итог на "
                "конец мая → дельта = май); период зафиксирован в поле period_month. Ф.123 (капитал) и "
                "Ф.135 (нормативы, %) — месячные, на дату. Реестр — 12 системно значимых банков; имена и "
                "значения из ЦБ. Не ИИР.",
    }
    if validation_issues:
        sys.stderr.write(f"[cbr] DQ-замечания ({len(validation_issues)}): "
                         + "; ".join(validation_issues[:8]) + ("…" if len(validation_issues) > 8 else "") + "\n")
    data_quality = {
        "generated_at": checked_at,
        "banks_total": len(banks),
        "banks_active": sum(1 for b in banks if b["is_active"]),
        "banks_sib": sum(1 for b in banks if b["is_systemically_important"]),
        "values_loaded": values_loaded, "missing": missing, "errors": errors,
        "last_report_date": last_dates["102"], "last_report_dates": last_dates,
        "is_stale": is_stale, "validation_status": validation_status,
        "validation_issues": validation_issues, "pipeline_version": PIPELINE_VERSION,
        "metrics": [{"metric_id": m["metric_id"], "name": m["display_name_ru"], "form": m["form"],
                     "symbol": m["symbol"], "reliability": m["reliability_level"]} for m in metrics],
        "unavailable_metrics": mapping.get("unavailable", []),
        "banks": dq_banks,
        "statuses_legend": ["ok", "missing", "stale", "parse_error", "source_unavailable",
                            "calculated", "official_direct", "calculated_from_official"],
    }

    if values_loaded == 0:
        sys.stderr.write("[cbr] СТОП: не загружено ни одного значения — не публикуем\n")
        return 1

    payloads = {"banks.json": banks, "bank_timeseries.json": timeseries,
                "metadata.json": metadata, "data_quality.json": data_quality,
                "metric_mapping.json": mapping}
    for fname, obj in payloads.items():
        with open(os.path.join(OUT, fname), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    sys.stderr.write(f"[cbr] OK → {OUT}: банков {len(banks)}, значений {values_loaded}, "
                     f"последние даты {last_dates}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
