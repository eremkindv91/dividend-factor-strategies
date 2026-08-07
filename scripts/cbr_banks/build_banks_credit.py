#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Кредитный портфель публичных банков из формы 101 ЦБ РФ (SOAP CreditOrgInfo).

Отвечает на вопрос «из чего сложен портфель банка и как он менялся»: кредиты юрлицам,
физлицам, индивидуальным предпринимателям, финансовым организациям и просроченная
задолженность — помесячно, из оборотной ведомости по счетам бухучёта.

Это РСБУ ОТДЕЛЬНОГО БАНКА, а не МСФО группы. Смешивать эти ряды в одном графике нельзя:
периметр разный (у Сбербанка группа включает дочерние банки и лизинг), классификация
разная, даты признания разные. Маркировка обязательна и уходит в meta.

Только stdlib: workflow update-cbr-banks.yml работает без pip install.

СЕМАНТИКА ФОРМЫ (выяснена на живом сервисе, а не по догадкам об именах)
  • vitg — ВХОДЯЩИЙ остаток, iitg — ИСХОДЯЩИЙ. Доказано рядом: iitg(месяц t) в точности
    равен vitg(месяц t+1) — проверено на SBER 45.2 за май–июнь 2026. Берётся iitg:
    именно он относится к отчётной дате.
  • ap — признак счёта: 1 (активный) и 2 (пассивный) приходят ОТДЕЛЬНЫМИ строками одного
    кода. Кредит — это актив; пассивная строка того же кода к портфелю не относится.
    Прошлая попытка взяла у Сбербанка 45.2 с ap=2 и получила «розницу 0,70 трлн ₽», что
    расходилось с МСФО в 27 раз. Правильное значение — 45.2 с ap=1: 19,30 трлн ₽.
  • Коды 45.0 / 45.1 / 45.2 — агрегаты ПУБЛИКУЕМОЙ формы. В официальном справочнике
    Form101IndicatorsEnum их нет: там только счета 450–459. Отдельные 452 (кредиты
    негосударственным коммерческим организациям) и 455 (кредиты физлицам) в публикации
    не раскрываются — по ним сервис молча возвращает пустоту, а их содержимое уходит
    в агрегаты. Интерпретация «45.0 — юрлица, 45.2 — физлица» подтверждается не
    справочником, а сходимостью величин с публичной отчётностью банков (Совкомбанк:
    1,33 + 1,30 = 2,63 трлн против ~2,6 трлн портфеля; БСП: 0,66 + 0,21 против ~0,8).
    Это сказано в meta прямым текстом — читатель вправе знать, где кончается официальное
    и начинается наше сопоставление.

ЧТО НЕ ПУБЛИКУЕТСЯ
  • Резервы и покрытие. Пассивные строки (ap=2) похожи на резервы по величине — у
    Сбербанка это 3,6% розницы и 4,4% корпоративного портфеля, — но подтверждения этому
    в справочнике ЦБ нет. Показать их как «резервы» значило бы выдать правдоподобную
    догадку за факт.
  • Stage 3, coverage ratio и cost of risk: в форме 101 их не существует в принципе.
    Отсутствующую метрику нельзя вывести из оборотной ведомости.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cbr_soap as cs  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).resolve().parent / "banks_config.json"
OUT = ROOT / "site" / "cbr" / "credit_portfolio.json"

# Код формы → (ключ в выходе, человеческое название, входит ли в валовый портфель).
# Названия счетов 450–459 взяты из официального справочника Form101IndicatorsEnum;
# у агрегатов 45.0/45.2 официального названия нет — см. оговорку в модуле и в meta.
PARTS = [
    ("45.0", "corporate", "Юридические лица", True),
    ("45.2", "retail", "Физические лица", True),
    ("454", "sole_proprietors", "Индивидуальные предприниматели", True),
    ("451", "financial_orgs", "Негосударственные финансовые организации", True),
    ("450", "state_orgs", "Организации в государственной собственности", True),
    ("453", "non_profit", "Негосударственные некоммерческие организации", True),
    ("458", "overdue", "Просроченная задолженность", True),
    ("459", "overdue_interest", "Просроченные проценты", False),
]
ACTIVE = "1"                      # признак активного счёта: кредит — это актив
MONTHS_BACK = 60                  # пять лет помесячно: длиннее ряд ISS всё равно не отдаёт ровно


def month_starts(count: int, today: datetime) -> list[str]:
    """Отчётные даты формы 101 — первые числа месяцев, от свежей к старой."""
    year, month = today.year, today.month
    out = []
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}-01")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return out


def parse_rows(xml: str) -> list[dict]:
    root = ET.fromstring(xml.encode("utf-8"))
    rows = []
    for parent in root.iter():
        kids = list(parent)
        if kids and all(len(k) == 0 for k in kids) and len(kids) >= 3:
            rows.append({cs._local(k.tag): (k.text or "").strip() for k in kids})
    return rows


def series(reg_num: int, ind_code: str, date_from: str, date_to: str) -> dict[str, float]:
    """Помесячный ряд исходящих остатков по активной стороне одного кода, тыс. ₽."""
    xml = cs.soap_post(
        "Data101FullV2",
        f'<Data101FullV2 xmlns="{cs.NS}"><CredorgNumber>{reg_num}</CredorgNumber>'
        f"<IndCode>{ind_code}</IndCode><DateFrom>{date_from}</DateFrom>"
        f"<DateTo>{date_to}</DateTo></Data101FullV2>",
    )
    out: dict[str, float] = {}
    for row in parse_rows(xml):
        if row.get("ap") != ACTIVE or not row.get("dt"):
            continue
        try:
            value = float(row.get("iitg") or 0)
        except ValueError:
            continue
        out[row["dt"][:10]] = value
    return out


def build_bank(bank: dict, date_from: str, date_to: str) -> dict:
    parts: dict[str, dict[str, float]] = {}
    for code, key, _label, _gross in PARTS:
        try:
            parts[key] = series(int(bank["regnum"]), code, date_from, date_to)
        except Exception as exc:  # noqa: BLE001 — один код не должен ронять банк
            sys.stderr.write(f"[credit] {bank['ticker']} {code}: {str(exc)[:120]}\n")
            parts[key] = {}

    dates = sorted({d for values in parts.values() for d in values})
    if not dates:
        return {"ticker": bank["ticker"], "name": bank["name"], "regnum": bank["regnum"],
                "status": "unavailable", "reason": "форма 101 по банку не публикуется"}

    gross_keys = [key for _code, key, _label, gross in PARTS if gross]
    rows = []
    for d in dates:
        item = {"d": d}
        for _code, key, _label, _gross in PARTS:
            value = parts[key].get(d)
            # Пропуск месяца — это «нет данных», а не ноль: подставленный ноль на графике
            # выглядел бы как обнуление портфеля.
            if value is not None:
                item[key] = round(value / 1e6, 3)          # тыс. ₽ → млрд ₽
        present = [item.get(k) for k in gross_keys if item.get(k) is not None]
        item["gross"] = round(sum(present), 3) if present else None
        rows.append(item)

    last = rows[-1]
    return {
        "ticker": bank["ticker"], "name": bank["name"], "regnum": bank["regnum"],
        "status": "ok", "as_of": last["d"], "rows": rows,
        "latest": {k: last.get(k) for k in ("gross", *[p[1] for p in PARTS])},
    }


def build(today: datetime | None = None) -> dict:
    today = today or datetime.now(timezone.utc)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    banks = config["banks"] if isinstance(config, dict) and "banks" in config else config
    dates = month_starts(MONTHS_BACK, today)
    date_from, date_to = dates[-1], dates[0]

    out_banks = []
    for bank in banks:
        out_banks.append(build_bank(bank, date_from, date_to))
    ok = [b for b in out_banks if b.get("status") == "ok"]

    return {
        "meta": {
            "generated_at": today.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "Банк России, форма 101 (оборотная ведомость), SOAP CreditOrgInfo",
            "source_url": "http://www.cbr.ru/CreditInfoWebServ/CreditOrgInfo.asmx",
            "accounting": "РСБУ, отдельный банк (не МСФО группы)",
            "unit": "млрд ₽",
            "balance": "исходящий остаток на отчётную дату (поле iitg)",
            "side": "только активная сторона счетов (ap=1): кредит — это актив",
            "aggregate_note": (
                "Коды 45.0 и 45.2 — агрегаты публикуемой формы; в официальном справочнике "
                "Form101IndicatorsEnum их нет, отдельные счета 452 и 455 в публикации не "
                "раскрываются. Отнесение 45.0 к юрлицам, а 45.2 к физлицам подтверждено "
                "не справочником, а сходимостью величин с публичной отчётностью банков."
            ),
            "not_published": (
                "Резервы и покрытие не публикуются: пассивные строки похожи на резервы по "
                "величине, но подтверждения в справочнике ЦБ нет. Stage 3, coverage и cost "
                "of risk в форме 101 отсутствуют."
            ),
            "banks_ok": len(ok),
            "banks_total": len(out_banks),
            "as_of": max((b["as_of"] for b in ok), default=None),
        },
        "banks": out_banks,
    }


# Метрики для общего селектора раздела «Банки РФ»: тот же справочник и тот же ряд, что
# у форм 102/123/135, — иначе кредитный портфель пришлось бы смотреть в отдельном месте,
# а сравнить его с прибылью и капиталом в одном интерфейсе было бы нельзя.
TS_METRICS = [
    ("gross", "credit_gross", "Кредитный портфель, всего", "45.0+45.2+451+453+454+458"),
    ("corporate", "credit_corporate", "Кредиты юридическим лицам", "45.0"),
    ("retail", "credit_retail", "Кредиты физическим лицам", "45.2"),
    ("sole_proprietors", "credit_sole_proprietors", "Кредиты индивидуальным предпринимателям", "454"),
    ("overdue", "credit_overdue", "Просроченная задолженность по кредитам", "458"),
]
TS_GROUP = "Кредитный портфель (Ф.101, месячная)"
AGGREGATE_CODES = {"45.0", "45.2"}


def merge_into_timeseries(payload: dict) -> tuple[int, int]:
    """Дописать ряды портфеля в общие bank_timeseries.json и metric_mapping.json.

    Файлы уже созданы build_cbr_banks.py — здесь именно дозапись, а не перегенерация:
    порядок шагов в workflow это гарантирует, а перезапись затёрла бы формы 102/123/135.
    """
    site_cbr = ROOT / "site" / "cbr"
    ts_path, map_path = site_cbr / "bank_timeseries.json", site_cbr / "metric_mapping.json"
    if not ts_path.exists() or not map_path.exists():
        sys.stderr.write("[credit] bank_timeseries.json/metric_mapping.json нет — слияние пропущено\n")
        return 0, 0

    ts = json.loads(ts_path.read_text(encoding="utf-8"))
    mapping = json.loads(map_path.read_text(encoding="utf-8"))

    added_rows = 0
    for bank in payload["banks"]:
        if bank.get("status") != "ok":
            continue
        reg = str(bank["regnum"])
        ts.setdefault(reg, {})
        for key, metric_id, _label, symbol in TS_METRICS:
            points = []
            for row in bank["rows"]:
                value = row.get(key)
                if not isinstance(value, (int, float)):
                    continue                       # пропуск месяца остаётся пропуском
                points.append({
                    "date": row["d"],
                    # млрд ₽ → тыс. ₽: единица общего ряда, иначе график смешает масштабы
                    "value": round(value * 1e6, 3),
                    "symbol": symbol, "form": "101", "unit": "тыс. руб.",
                    "quality_status": "official_direct",
                    "source": "CBR CreditOrgInfo.asmx (Data101FullV2), активная сторона счетов",
                })
            if points:
                ts[reg][metric_id] = points
                added_rows += len(points)

    known = {m.get("metric_id") for m in mapping.get("metrics", [])}
    added_metrics = 0
    for _key, metric_id, label, symbol in TS_METRICS:
        if metric_id in known:
            continue
        note = f"Коды формы 101: {symbol}. Исходящий остаток, только активная сторона счетов."
        if AGGREGATE_CODES & set(symbol.split("+")):
            note += (" Коды 45.0/45.2 — агрегаты публикуемой формы, в справочнике ЦБ их нет;"
                     " отнесение к юрлицам и физлицам подтверждено сходимостью с отчётностью банков.")
        mapping.setdefault("metrics", []).append({
            "metric_id": metric_id, "display_name_ru": label, "group": TS_GROUP,
            "form": "101", "symbol": symbol, "unit": "тыс. руб.", "scale": 1,
            "cumulative": False, "calculation_method": "official_direct",
            "reliability_level": "official_direct", "notes": note,
        })
        added_metrics += 1

    ts_path.write_text(json.dumps(ts, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    map_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
    return added_metrics, added_rows


def main() -> int:
    payload = build()
    if not payload["meta"]["banks_ok"]:
        sys.stderr.write("[credit] ни одного банка — файл не перезаписываем\n")
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    metrics, rows = merge_into_timeseries(payload)
    meta = payload["meta"]
    print(f"[credit] {meta['banks_ok']} из {meta['banks_total']} банков, "
          f"последняя дата {meta['as_of']} → {OUT}")
    print(f"[credit] в общий ряд добавлено метрик: {metrics}, точек: {rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
