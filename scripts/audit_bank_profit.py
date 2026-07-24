#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Диагностический аудит месячной прибыли банка (форма 102 ЦБ РФ) — воспроизводимая сверка
raw → transformed → frontend без ручной подгонки значений.

Использование:
    python scripts/audit_bank_profit.py --bank "Совкомбанк" --from 2025-01 --to 2026-06
    python scripts/audit_bank_profit.py --bank 963 --live --format markdown

Что делает:
  • находит банк (по имени или рег.№) в scripts/cbr_banks/banks_config.json, печатает рег.№;
  • читает опубликованный ряд из site/cbr/bank_timeseries.json (raw накопл. `value`, transformed
    `value_q`, метка периода `period_month`);
  • НЕЗАВИСИМО пересчитывает месячную прибыль из накопленного (сброс на январь) и сверяет с value_q;
  • проверяет period_month = отчётная дата − 1 мес (корректность привязки «за период» к календарю);
  • ищет дубли отчётных дат, пропуски месяцев, сдвиг ±1 (метка ≠ истинный период);
  • при --live дополнительно тянет сырьё из ЦБ (GetDatesForF102/Data102F) как источник истины;
  • печатает дату генерации набора и is_stale;
  • exit≠0 при любом расхождении выше --tolerance (по умолчанию 1 тыс. руб.).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CONFIG = os.path.join(HERE, "cbr_banks", "banks_config.json")
TS = os.path.join(REPO, "site", "cbr", "bank_timeseries.json")
META = os.path.join(REPO, "site", "cbr", "metadata.json")
PROFIT_METRIC = "net_profit"   # символ 61101 Ф.102

RU_MON = ["", "янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def accum_year(dt: str) -> int:
    y, m = int(dt[:4]), int(dt[5:7])
    return y if m > 1 else y - 1


def prev_month(dt: str) -> str:
    y, m = int(dt[:4]), int(dt[5:7])
    y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return f"{y:04d}-{m:02d}"


def mon_label(ym: str) -> str:
    y, m = ym[:4], int(ym[5:7])
    return f"{RU_MON[m]} {y}"


def find_bank(cfg: dict, key: str) -> dict | None:
    banks = cfg["banks"] if isinstance(cfg, dict) else cfg
    key_l = str(key).lower()
    for b in banks:
        if str(b.get("regnum")) == str(key):
            return b
        nm = (b.get("name") or "").lower()
        if key_l in nm or nm in key_l:
            return b
    return None


def recompute_monthly(points: list[dict]) -> dict[str, float]:
    """Независимый пересчёт «за период» из накопленного `value` (сброс на первый месяц года накопления).
    Возвращает {report_date: monthly_delta}."""
    pts = sorted(points, key=lambda p: p["date"])
    out, prev = {}, None
    for p in pts:
        if prev is not None and accum_year(prev["date"]) == accum_year(p["date"]):
            out[p["date"]] = round(p["value"] - prev["value"], 2)
        else:
            out[p["date"]] = p["value"]
        prev = p
    return out


def cbr_raw(regnum: int, frm: str, to: str) -> dict[str, float]:
    """Источник истины: накопл. 61101 Ф.102 напрямую из ЦБ за окно [frm, to] (по отчётным датам)."""
    sys.path.insert(0, os.path.join(HERE, "cbr_banks"))
    from cbr_soap import data102f, get_dates_for_form  # noqa: E402
    dates = [d for d in get_dates_for_form("102", regnum) if frm <= d[:7] <= to]
    raw = {}
    for dt in dates:
        d = data102f(regnum, dt)
        v = next((r["value"] for r in (d["rows"] if d else []) if r["symbol"] == "61101"), None)
        if v is not None:
            raw[dt] = v
    return raw


def in_window(dt: str, frm: str, to: str) -> bool:
    return frm <= dt[:7] <= to


def main() -> int:
    ap = argparse.ArgumentParser(description="Аудит месячной прибыли банка (Ф.102 ЦБ РФ)")
    ap.add_argument("--bank", required=True, help="имя (частично) или рег.№")
    ap.add_argument("--from", dest="frm", default="2025-01", help="YYYY-MM")
    ap.add_argument("--to", dest="to", default=date.today().strftime("%Y-%m"), help="YYYY-MM")
    ap.add_argument("--tolerance", type=float, default=1.0, help="допуск расхождения, тыс. руб.")
    ap.add_argument("--format", choices=["json", "markdown"], default="markdown")
    ap.add_argument("--live", action="store_true", help="дополнительно сверить с сырьём ЦБ (сеть)")
    a = ap.parse_args()

    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    bank = find_bank(cfg, a.bank)
    if not bank:
        sys.stderr.write(f"[audit] банк «{a.bank}» не найден в banks_config.json\n")
        return 2
    reg = int(bank["regnum"])

    with open(TS, encoding="utf-8") as f:
        ts = json.load(f)
    meta = json.load(open(META, encoding="utf-8")) if os.path.exists(META) else {}
    points = ((ts.get(str(reg)) or {}).get(PROFIT_METRIC)) or []
    win = [p for p in points if in_window(p["date"], a.frm, a.to)]

    recomputed = recompute_monthly(points)          # из всего ряда, чтобы дельта на границе окна была верной
    issues, rows = [], []
    dates = [p["date"] for p in win]
    if len(dates) != len(set(dates)):
        issues.append("дубли отчётных дат")

    for p in win:
        d = p["date"]
        raw_cum = p.get("value")
        transformed = p.get("value_q")
        recomp = recomputed.get(d)
        pm = p.get("period_month")
        pm_expected = prev_month(d)
        true_period = pm or pm_expected            # к какому кал. месяцу ОТНОСИТСЯ дельта
        frontend_label_new = mon_label(true_period)          # как подпишет исправленный фронт
        # расхождения
        calc_mismatch = (transformed is not None and recomp is not None
                         and abs(transformed - recomp) > a.tolerance)
        pm_mismatch = (pm is not None and pm != pm_expected)
        if calc_mismatch:
            issues.append(f"{d}: value_q≠пересчёт ({transformed} vs {recomp})")
        if pm_mismatch:
            issues.append(f"{d}: period_month {pm}≠{pm_expected}")
        rows.append({
            "report_date": d,
            "raw_cumulative": raw_cum,
            "recomputed_monthly": recomp,
            "json_value_q": transformed,
            "true_period_month": true_period,
            "frontend_label": frontend_label_new,
            "period_month_field": pm,
            "status": "OK" if not (calc_mismatch or pm_mismatch) else "MISMATCH",
        })

    # пропуски месяцев внутри окна (по отчётным датам)
    rep_months = sorted({d[:7] for d in dates})
    for i in range(1, len(rep_months)):
        y0, m0 = int(rep_months[i - 1][:4]), int(rep_months[i - 1][5:7])
        exp = f"{y0 + (1 if m0 == 12 else 0):04d}-{(1 if m0 == 12 else m0 + 1):02d}"
        if rep_months[i] != exp:
            issues.append(f"пропуск между {rep_months[i-1]} и {rep_months[i]}")

    live_block = None
    if a.live:
        try:
            raw = cbr_raw(reg, a.frm, a.to)
            live_rows, live_issues = [], []
            for d in sorted(raw):
                local = next((p["value"] for p in win if p["date"] == d), None)
                match = local is not None and abs(local - raw[d]) <= a.tolerance
                if local is not None and not match:
                    live_issues.append(f"{d}: сайт {local} ≠ ЦБ {raw[d]}")
                live_rows.append({"report_date": d, "cbr_raw_cumulative": raw[d],
                                  "site_cumulative": local, "match": match})
            # истинная последняя месячная прибыль из ЦБ (может быть свежее сайта)
            live_monthly = {}
            sd = sorted(raw)
            for i, d in enumerate(sd):
                if i and accum_year(sd[i - 1]) == accum_year(d):
                    live_monthly[prev_month(d)] = round(raw[d] - raw[sd[i - 1]], 2)
            live_block = {"cbr_rows": live_rows, "cbr_monthly_by_period": live_monthly,
                          "cbr_latest_report_date": sd[-1] if sd else None, "issues": live_issues}
            issues += live_issues
        except Exception as e:  # noqa: BLE001
            live_block = {"error": f"{type(e).__name__}: {str(e)[:120]}"}

    report = {
        "bank": bank.get("name"), "reg_number": reg, "ticker": bank.get("ticker"),
        "expected_name": bank.get("expected_name"),
        "window": {"from": a.frm, "to": a.to},
        "dataset_generated_at": meta.get("generated_at"),
        "dataset_latest_report_date": meta.get("last_report_date"),
        "dataset_is_stale": meta.get("is_stale"),
        "dataset_pipeline_version": meta.get("pipeline_version"),
        "tolerance_thousand_rub": a.tolerance,
        "rows": rows, "issues": issues, "live": live_block,
        "verdict": "OK" if not issues else "DISCREPANCY",
    }

    if a.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"# Аудит прибыли: {report['bank']} (рег.№ {reg}, {bank.get('ticker')})\n")
        print(f"Окно: {a.frm}…{a.to} · набор от {report['dataset_generated_at']} · "
              f"последняя отчётная дата {report['dataset_latest_report_date']} · "
              f"is_stale={report['dataset_is_stale']} · {report['dataset_pipeline_version']}\n")
        print("| отчётная дата | raw накопл. (тыс) | пересчёт мес. | value_q (JSON) | истинный период | метка фронта | статус |")
        print("|---|---:|---:|---:|---|---|---|")
        for r in rows:
            print(f"| {r['report_date']} | {r['raw_cumulative']:,} | "
                  f"{'' if r['recomputed_monthly'] is None else format(r['recomputed_monthly'], ',')} | "
                  f"{'' if r['json_value_q'] is None else format(r['json_value_q'], ',')} | "
                  f"{r['true_period_month']} | {r['frontend_label']} | {r['status']} |")
        if live_block and "cbr_monthly_by_period" in live_block:
            print("\n**Сверка с ЦБ (live), месячная прибыль по истинному периоду:**")
            for pm, v in sorted(live_block["cbr_monthly_by_period"].items()):
                print(f"- {mon_label(pm)}: {v:,} тыс ₽ ({v/1e6:.2f} млрд)")
            if live_block.get("cbr_latest_report_date"):
                print(f"- последняя отчётная дата у ЦБ: **{live_block['cbr_latest_report_date']}**")
        print(f"\n**Вердикт: {report['verdict']}**" + (f" · замечаний: {len(issues)}" if issues else ""))
        for it in issues:
            print(f"  - {it}")

    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
