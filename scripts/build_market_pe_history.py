#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""История агрегированного P/E Индекса МосБиржи: дорого ли сейчас относительно своей нормы.

Одно текущее число («P/E рынка 4,4») не отвечает на вопрос, ради которого его смотрят: это
дорого или дёшево ДЛЯ ЭТОГО рынка. Модуль строит месячный ряд, медиану, перцентиль текущего
значения и декомпозицию «цена против прибыли» — стало дешевле из-за падения цен или из-за
роста прибыли.

═══ НА ЧЁМ ЭТО ПОСТРОЕНО ═══

Состав корзины — ИСТОРИЧЕСКИЙ, на каждую дату свой (ISS statistics/.../analytics/IMOEX?date=…,
доступен с 2001 года). Это принципиально: проекция сегодняшних имён в прошлое дала бы
survivorship bias — выбывшие компании исчезли бы из истории, а сегодняшние «всегда были»
в индексе. Здесь такого нет: в каждом месяце ровно те бумаги, что реально были в индексе.

Капитализация — POINT-IN-TIME от самой биржи (DAILYCAPITALIZATION в history/.../totals).
Не реконструкция «цена × сегодняшнее число акций»: в реестре MOEX лежит контемпоральное
ISSUESIZE, поэтому допэмиссии и выкупы учтены. Проверено на VTBR: 2016-04 → 12,96 трлн акций
и 968 млрд ₽ капитализации против 12,93 млрд акций сегодня (обратный сплит 5000:1 + допэмиссия
2023). Реконструкция по текущему числу акций завысила бы VTBR 2016 года в пять раз.

═══ ЧЕСТНЫЕ ГРАНИЦЫ (читать до использования цифр) ═══

1. FULL-CAP, НЕ INDEX-LIKE. DAILYCAPITALIZATION — полная капитализация, без free-float и без
   индексных коэффициентов MOEX. Это P/E корзины индекса, но НЕ официальный P/E индекса.

2. ДАТА РАСКРЫТИЯ ОТЧЁТНОСТИ — ДОПУЩЕНИЕ. В фундамент-слое есть только fiscal_year, поля
   published_at нет ни у одной из 3044 записей. Брать конец отчётного периода как дату
   доступности нельзя — годовой отчёт выходит весной следующего года, и такой ряд имел бы
   look-ahead bias. Поэтому введено ЯВНОЕ допущение: прибыль за FY считается известной рынку
   с 1 апреля FY+1. Помечено в данных как assumption, за факт не выдаётся.

3. ПРИБЫЛЬ ГОДОВАЯ, НЕ TTM. Квартальных данных в слое нет. Ряд ступенчатый: знаменатель
   меняется раз в год на дату допущения, числитель — ежемесячно.

4. ИСТОЧНИК ПРИБЫЛИ — SmartLab с частичной ручной сверкой МСФО. Доля сверенного считается
   отдельно (verified_coverage_pct) и всегда от ВСЕЙ корзины, а не от покрытой части.

5. УБЫТКИ ВКЛЮЧАЮТСЯ. Отрицательная прибыль входит в знаменатель со своим знаком — исключать
   её значило бы завышать прибыль рынка. Если сумма ≤ 0, P/E не определён и так и помечается.

6. ПОКРЫТИЕ ПАДАЕТ ВГЛУБЬ ИСТОРИИ. Выбывшие эмитенты прошлых лет в фундамент-слое often
   отсутствуют. Месяцы с покрытием ниже порога помечаются и в выводы не идут.

Запуск:  python scripts/build_market_pe_history.py [--full]
Выход:   site/market_pe_history.json  (+ кэш data/market_pe_history_cache.json)
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build_market_pe as bp          # noqa: E402  универсум, слой прибыли, fetch_json с ретраями
from build_market_pe import earnings_defects  # noqa: E402  одно правило отбора на обе карточки

OUT = os.path.join(ROOT, "site", "market_pe_history.json")
CACHE = os.path.join(ROOT, "data", "market_pe_history_cache.json")
MSK = timezone(timedelta(hours=3))

ISS_COMP = ("https://iss.moex.com/iss/statistics/engines/stock/markets/index/analytics/IMOEX.json"
            "?date={d}&limit=100&iss.meta=off&iss.only=analytics,analytics.cursor")
ISS_CAP = ("https://iss.moex.com/iss/history/engines/stock/totals/boards/MRKT/securities.json"
           "?date={d}&iss.meta=off&iss.only=securities&securities.columns=SECID,DAILYCAPITALIZATION")

START_MONTH = "2012-04"           # первая дата, где прибыль FY2011 уже «раскрыта» по допущению
DISCLOSURE_MONTH = 4              # прибыль за FY доступна с 1 апреля FY+1 (допущение, см. шапку)
MIN_POINTS_FOR_PERCENTILE = 36    # меньше 3 лет — перцентиль статистически бессодержателен
COVERAGE_MIN = 60.0               # ниже — месяц не участвует в медиане/перцентиле
COVERAGE_HIGH = 85.0              # verified: доля сверенной прибыли
COVERAGE_OK = 70.0
PE_SANE = (0.5, 60.0)             # вне коридора — почти наверняка дефект данных, а не рынок
METRICS = ("reported", "normalized", "ocf")   # прибыль · прибыль по средней марже · ден. поток
NORM_MIN_YEARS = 4                # меньше — средняя рентабельность ничего не описывает
NORM_WINDOW = 10                  # окно усреднения маржи: примерно экономический цикл
OCF_MAX_LAG_YEARS = 1             # насколько ден. поток может отставать от свежей отчётности
SCHEMA_VERSION = 2                # 2: кэш капитализации перекладкой на срезы по датам (было по тикерам)


def log(msg: str) -> None:
    sys.stderr.write(f"[pe-hist] {msg}\n")


def month_iter(start: str, end: str):
    y, m = int(start[:4]), int(start[5:7])
    while f"{y:04d}-{m:02d}" <= end:
        yield f"{y:04d}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def month_last_day(month: str) -> date:
    y, m = int(month[:4]), int(month[5:7])
    return date(y + (m == 12), 1 if m == 12 else m + 1, 1) - timedelta(days=1)


def load_cache() -> dict:
    try:
        with open(CACHE, encoding="utf-8") as fh:
            c = json.load(fh)
        if c.get("schema_version") == SCHEMA_VERSION:
            return c
    except (OSError, ValueError):
        pass
    return {"schema_version": SCHEMA_VERSION, "composition": {}, "caps": {}}


def composition_for(month: str, cache: dict) -> tuple[list[str], str | None]:
    """Состав индекса на последний торговый день месяца. ISS на нерабочую дату отдаёт пусто —
    отступаем назад по календарю (не больше недели: длиннее в РФ праздников не бывает)."""
    hit = cache["composition"].get(month)
    if hit:
        return hit["tickers"], hit["date"]
    probe = month_last_day(month)
    for _ in range(8):
        payload = bp.fetch_json(ISS_COMP.format(d=probe.isoformat()))
        block = payload.get("analytics", {})
        cols = {c: i for i, c in enumerate(block.get("columns", []))}
        rows = block.get("data", [])
        if rows and "ticker" in cols:
            tickers = sorted({str(r[cols["ticker"]]).upper() for r in rows if r[cols["ticker"]]})
            asof = str(rows[0][cols["tradedate"]]) if "tradedate" in cols else probe.isoformat()
            cache["composition"][month] = {"tickers": tickers, "date": asof}
            return tickers, asof
        probe -= timedelta(days=1)
    return [], None


def caps_on(asof: str, cache: dict) -> dict[str, float]:
    """{SECID: полная капитализация} на торговую дату — срез реестра MOEX.

    Запрашиваем СРЕЗ ПО ДАТЕ (все бумаги разом), а не историю одной бумаги за все годы.
    Так надёжнее: 14-летние запросы по одному тикеру ISS отдаёт нестабильно — часть
    обрывается по таймауту, и история молча укорачивается на произвольном месяце (проверено:
    из 103 бумаг до текущего месяца доходили 19, у AFLT ряд обрывался на 2022-07, хотя
    данные за 2026-07 у биржи есть). Плюс это на два порядка меньше запросов: один на месяц
    вместо одного на бумагу, и дата капитализации совпадает с датой состава индекса.
    """
    hit = cache["caps"].get(asof)
    if hit is not None:
        return hit
    payload = bp.fetch_json(ISS_CAP.format(d=asof))
    block = payload.get("securities", {})
    cols = {c: i for i, c in enumerate(block.get("columns", []))}
    out: dict[str, float] = {}
    if "DAILYCAPITALIZATION" in cols and "SECID" in cols:
        for r in block.get("data", []):
            cap = r[cols["DAILYCAPITALIZATION"]]
            if isinstance(cap, (int, float)) and cap > 0:
                out[str(r[cols["SECID"]])] = float(cap)
    cache["caps"][asof] = out
    return out


def normalized_earnings(series: list[dict], when: date):
    """Прибыль по средней за цикл рентабельности: median(прибыль/выручка) × текущая выручка.

    Зачем это нужно: один удачный или провальный год перекашивает оценку всего рынка. В
    апреле 2021 P/E корзины доходил до 16 не потому, что рынок стал дорогим, а потому что в
    знаменателе стояла ковидная прибыль 2020 года. Усреднение по рентабельности убирает этот
    эффект — и остаётся в текущих рублях: поправка на инфляцию не нужна, поскольку и маржа,
    и выручка берутся в номинале своего года.

    Почему не CAPE Шиллера: он усредняет прибыль за 10 лет в ПОСТОЯННЫХ ценах, а ряд ИПЦ у
    Банка России начинается с 2015 года. Дефлятора нужной глубины нет, а усреднять
    номинальные рубли при российской инфляции — значит занижать прошлые годы.
    """
    rows = [r for r in series if date(int(r["fy"]) + 1, DISCLOSURE_MONTH, 1) <= when]
    margins = [r["value"] / r["revenue"] for r in rows
               if r.get("revenue") and r["revenue"] > 0 and r.get("value") is not None]
    if len(margins) < NORM_MIN_YEARS or not rows:
        return None
    base_revenue = rows[-1].get("revenue")
    if not base_revenue or base_revenue <= 0:
        return None
    return statistics.median(margins[-NORM_WINDOW:]) * base_revenue


def cash_flow(series: list[dict], when: date, latest_fy: int):
    """Операционный денежный поток — заработок без бумажных переоценок и списаний.

    Строка денежного потока за последний отчётный год заполнена в слое не у всех (на июль
    2026 — у 11 эмитентов из 39 против 39 по прибыли), поэтому допускаем отставание, но
    НЕ БОЛЬШЕ ОДНОГО ГОДА от последней раскрытой отчётности самого эмитента. Без этого
    ограничения в одно число попадали бы потоки за 2021 и за 2025 год (проверено: шесть
    эмитентов тянули данные пятилетней давности), а такая смесь винтажей ничего не измеряет.
    """
    for rec in reversed(series):
        fy = int(rec["fy"])
        if date(fy + 1, DISCLOSURE_MONTH, 1) > when:
            continue                                 # ещё не раскрыт — look-ahead
        if fy < latest_fy - OCF_MAX_LAG_YEARS:
            break                                    # дальше только глубже — смысла нет
        v = rec.get("operating_cash_flow")
        if isinstance(v, (int, float)):
            return float(v), fy
    return None, None


def _metric_status(pe, priced, coverage, verified_coverage):
    """Статус метрики. Ступени verified/mixed_sources доступны только там, где сверка
    вообще проводилась — у прибыли; для остальных знаменателей потолок «estimated»."""
    if pe is None:
        return "invalid_denominator"
    if priced < COVERAGE_MIN or coverage < COVERAGE_MIN:
        return "insufficient_coverage"
    if verified_coverage >= COVERAGE_HIGH:
        return "verified"
    if verified_coverage >= COVERAGE_OK:
        return "mixed_sources"
    return "estimated"


def aggregate_month(month: str, tickers: list[str], caps_at: dict[str, float],
                    hist: dict, base_of: dict, no_earnings: dict | None = None,
                    rejected: list | None = None) -> dict | None:
    """Агрегированный P/E корзины за один месяц: ΣКапитализация ÷ ΣПрибыль.

    Считаем ПО ЭМИТЕНТАМ, а не по бумагам. В корзине IMOEX обыкновенные и привилегированные
    акции одного эмитента идут отдельными строками (SBER+SBERP, SNGS+SNGSP, TATN+TATNP), а
    фундамент-слой хранит одно и то же значение прибыли под каждым классом. Складывая по
    бумагам, знаменатель получаешь задвоенным, и P/E рынка занижается. Группировка по
    base_ticker убирает это структурно: капитализация эмитента — сумма его классов,
    прибыль — одна запись.
    """
    when = month_last_day(month)
    issuers: dict[str, dict] = {}
    for tk in tickers:
        cap = caps_at.get(tk)
        if cap is None:
            continue
        node = issuers.setdefault(base_of.get(tk, tk), {"cap": 0.0, "secids": []})
        node["cap"] += cap
        node["secids"].append(tk)

    cap_total, last_fy = 0.0, None
    # Три знаменателя оценки считаются за один проход по эмитентам: капитализация у них
    # общая, а покрытие — своё, потому что выручка и денежный поток есть не у всех.
    acc = {m: {"cap": 0.0, "verified": 0.0, "sum": 0.0, "n": 0} for m in METRICS}
    ocf_years: list[int] = []
    for issuer, node in issuers.items():
        cap = node["cap"]
        cap_total += cap
        if issuer not in node["secids"]:
            # На бирже нет обыкновенных акций эмитента (Транснефть: торгуется только TRNFP,
            # обыкновенные у государства). Биржевая капитализация покрывает лишь часть
            # компании, а прибыль в слое — вся: такой эмитент занижал бы агрегированный P/E.
            if rejected is not None:
                rejected.append({"month": month, "ticker": issuer,
                                 "reason": "обыкновенные акции не торгуются — "
                                           "биржевая капитализация не покрывает компанию"})
            continue
        rec, series = None, []
        for key in (issuer, *node["secids"]):        # прибыль лежит под тикером класса (ТРНФ → TRNFP)
            series = hist.get(key) or []
            rec = earnings_available_at(series, when)
            if rec:
                break
        if rec is None:
            if no_earnings is not None:
                for tk in node["secids"]:
                    no_earnings[tk] = no_earnings.get(tk, 0) + 1
            continue
        defects = earnings_defects(rec, series)
        if defects:
            if rejected is not None:
                rejected.append({"month": month, "ticker": issuer, "fy": int(rec["fy"]),
                                 "value": float(rec["value"]), "reason": "; ".join(defects)})
            continue
        verified = (rec.get("verification_status") == "verified"
                    or rec.get("source") == "verified_ifrs_seed")
        ocf_value, ocf_fy = cash_flow(series, when, int(rec["fy"]))
        values = {"reported": float(rec["value"]),          # убыток входит со своим знаком
                  "normalized": normalized_earnings(series, when),
                  "ocf": ocf_value}
        if ocf_fy is not None:
            ocf_years.append(ocf_fy)
        for metric, value in values.items():
            if value is None:                                # у метрики нет данных по эмитенту
                continue
            node_acc = acc[metric]
            node_acc["cap"] += cap
            node_acc["sum"] += value
            node_acc["n"] += 1
            # Ручная сверка (IFRS-seed) касается ТОЛЬКО чистой прибыли. Переносить её на
            # нормализацию и денежный поток нельзя: выручку и поток никто не сверял, и
            # подпись «сверено 73 %» под ними была бы неправдой.
            if verified and metric == "reported":
                node_acc["verified"] += cap
        last_fy = max(last_fy or 0, int(rec["fy"]))

    if cap_total <= 0 or acc["reported"]["n"] == 0:
        return None
    # Бумаги без капитализации не попадают и в cap_total, поэтому coverage их не видит: месяц,
    # где у реестра нашлись данные лишь по трём бумагам из 46, показывал «покрытие 100 %».
    # Меряем это отдельно — доля корзины, для которой капитализация вообще известна.
    priced = 100.0 * len(caps_at) / len(tickers) if tickers else 0.0

    metrics = {}
    for metric, a in acc.items():
        cov = 100.0 * a["cap"] / cap_total
        ver = 100.0 * a["verified"] / cap_total       # от ВСЕЙ корзины, не от покрытой
        value = None
        if a["sum"] > 0 and a["n"]:
            candidate = a["cap"] / a["sum"]
            if PE_SANE[0] <= candidate <= PE_SANE[1]:
                value = round(candidate, 3)
        metrics[metric] = {
            "value": value,
            "yield_pct": round(100.0 / value, 3) if value else None,
            "market_cap": round(a["cap"]), "denominator": round(a["sum"]),
            "coverage_pct": round(cov, 1), "verified_coverage_pct": round(ver, 1),
            "constituents_used": a["n"],
            "quality_status": _metric_status(value, priced, cov, ver),
        }
    if ocf_years:      # у денежного потока винтаж отчётности разный — показываем диапазон
        metrics["ocf"]["fiscal_years"] = [min(ocf_years), max(ocf_years)]

    base = metrics["reported"]           # верхний уровень — «Прибыль»: контракт для старого фронта
    pe, coverage = base["value"], base["coverage_pct"]
    verified_coverage, status = base["verified_coverage_pct"], base["quality_status"]
    cap_covered, earn_sum, n_incl = base["market_cap"], base["denominator"], base["constituents_used"]
    return {
        "metrics": metrics,
        "month": month, "as_of": None, "pe": pe,
        "earnings_yield_pct": round(100.0 / pe, 3) if pe else None,
        "market_cap": round(cap_covered), "earnings": round(earn_sum),
        "coverage_pct": round(coverage, 1),
        "verified_coverage_pct": round(verified_coverage, 1),
        "priced_pct": round(priced, 1),
        "constituents_total": len(tickers), "constituents_priced": len(caps_at), "constituents_used": n_incl,
        "last_fiscal_year": last_fy, "quality_status": status,
    }


def earnings_available_at(rows: list[dict], when: date) -> dict | None:
    """Последняя отчётность, которая БЫЛА БЫ известна рынку на дату `when` (защита от look-ahead)."""
    best = None
    for row in rows:
        if date(int(row["fy"]) + 1, DISCLOSURE_MONTH, 1) <= when:
            if best is None or row["fy"] > best["fy"]:
                best = row
    return best


def main() -> int:
    full = "--full" in sys.argv
    hist, fin_meta = bp.income_history()
    _classes, base_of = bp.build_masters()
    cache = {"schema_version": SCHEMA_VERSION, "composition": {}, "caps": {}} if full else load_cache()

    today = date.today()
    end_month = f"{today.year:04d}-{today.month:02d}"
    months = list(month_iter(START_MONTH, end_month))
    # текущий месяц ещё идёт: его состав и капитализацию перечитываем всегда
    cache["composition"].pop(end_month, None)

    comps: dict[str, tuple[list[str], str | None]] = {}
    universe: set[str] = set()
    for i, m in enumerate(months, 1):
        tickers, asof = composition_for(m, cache)
        comps[m] = (tickers, asof)
        universe.update(tickers)
        if i % 24 == 0:                  # ISS троттлит: обрыв на середине не должен обнулять работу
            save_cache(cache)
            log(f"  состав: {i}/{len(months)} мес.")
    save_cache(cache)
    log(f"состав получен за {sum(1 for m in months if comps[m][0])} мес., уникальных бумаг {len(universe)}")


    points, no_earnings, missing_cap, rejected = [], {}, [], []
    for i, m in enumerate(months, 1):
        tickers, asof = comps[m]
        if not tickers or not asof:
            continue
        try:
            snapshot = caps_on(asof, cache)
        except Exception as exc:  # noqa: BLE001
            missing_cap.append(f"{m}: срез капитализации не получен ({str(exc)[:40]})")
            continue
        caps_at = {tk: snapshot[tk] for tk in tickers if tk in snapshot}
        gap = sorted(set(tickers) - set(caps_at))
        if gap:
            # бумага в индексе, но без капитализации в реестре: её вес уходит из знаменателя
            # покрытия — фиксируем, чтобы это не выглядело как «покрыто 100%»
            missing_cap.append(f"{m}: без капитализации {', '.join(gap[:6])}")
        point = aggregate_month(m, tickers, caps_at, hist, base_of, no_earnings, rejected)
        if point:
            point["as_of"] = asof
            points.append(point)
        if i % 24 == 0:
            save_cache(cache)
            log(f"  капитализация: {i}/{len(months)} мес.")
    save_cache(cache)

    valid = [p for p in points if p["pe"] is not None and p["quality_status"] != "insufficient_coverage"]
    if not valid:
        log("нет ни одной валидной точки — прошлый файл не перезаписан")
        return 1
    current = valid[-1]

    def window(n_months):
        # Срез по количеству точек, а не по календарю: карточка на фронте рисует ровно
        # последние n точек, и статистика должна описывать то, что видно на графике.
        rows = valid if n_months is None else valid[-n_months:]
        if len(rows) < 3:
            return None
        vals = sorted(r["pe"] for r in rows)
        return {
            "months": len(rows), "from": rows[0]["month"], "to": rows[-1]["month"],
            "median": round(statistics.median(vals), 3),
            "p10": round(quantile(vals, .10), 3), "p25": round(quantile(vals, .25), 3),
            "p75": round(quantile(vals, .75), 3), "p90": round(quantile(vals, .90), 3),
            "min": vals[0], "max": vals[-1],
            "percentile_of_current": round(100.0 * sum(1 for x in vals if x <= current["pe"]) / len(vals), 1),
            "vs_median_pct": round(100.0 * (current["pe"] / statistics.median(vals) - 1), 1),
            "enough_for_percentile": len(rows) >= MIN_POINTS_FOR_PERCENTILE,
        }

    rfr = risk_free_pct()
    ey = current["earnings_yield_pct"]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "methodology": "full_cap_point_in_time",
        "methodology_name": "P/E корзины IMOEX по полной капитализации MOEX (point-in-time)",
        "universe": "historical_imoex",
        "universe_name": "исторический состав Индекса МосБиржи на каждую дату",
        "cap_source": "MOEX ISS DAILYCAPITALIZATION (контемпоральное число акций)",
        "disclosure_assumption": {
            "rule": f"прибыль за FY считается известной с 1.{DISCLOSURE_MONTH:02d} следующего года",
            "is_assumption": True,
            "why": ("в фундамент-слое нет published_at ни у одной записи; конец отчётного периода "
                    "как дату доступности брать нельзя — это дало бы look-ahead bias"),
        },
        "earnings_basis": "annual_net_income_not_ttm",
        "earnings_source": "SmartLab с частичной ручной сверкой МСФО",
        "current": {
            "month": current["month"], "as_of": current["as_of"], "pe": current["pe"],
            "earnings_yield_pct": ey, "risk_free_pct": rfr,
            "earnings_yield_spread_pp": round(ey - rfr, 2) if (rfr is not None and ey) else None,
            "coverage_pct": current["coverage_pct"],
            "verified_coverage_pct": current["verified_coverage_pct"],
            "priced_pct": current["priced_pct"],
            "constituents_priced": current["constituents_priced"],
            "constituents_used": current["constituents_used"],
            "constituents_total": current["constituents_total"],
            "last_fiscal_year": current["last_fiscal_year"],
            "quality_status": current["quality_status"],
        },
        # окна описывают метрику «Прибыль»: карточка считает статистику выбранного знаменателя
        # на клиенте, здесь — метаданные для внешних потребителей
        "windows_metric": "reported",
        "windows": {"3y": window(36), "5y": window(60), "10y": window(120), "all": window(None)},
        "decomposition": decompose(valid),
        "history": points,
        "diagnostics": {
            "months_total": len(points), "months_valid": len(valid),
            "cap_unavailable": sorted(missing_cap)[:40],
            "implausible_earnings": rejected[:40],
            "implausible_earnings_n": len(rejected),
            "no_earnings_top": sorted(no_earnings.items(), key=lambda kv: -kv[1])[:25],
        },
        "limitations": [
            "full-cap, а не index-like: free-float и индексные коэффициенты MOEX недоступны",
            "дата раскрытия отчётности — допущение (1 апреля FY+1), фактических published_at нет",
            "прибыль годовая, не TTM: квартальных данных в слое нет",
            "прибыль из SmartLab, сверена вручную частично — см. verified_coverage_pct",
            "чем глубже история, тем ниже покрытие: у выбывших эмитентов прибыли в слое нет",
        ],
        "generated_at": datetime.now(MSK).replace(microsecond=0).isoformat(),
        "fundamentals_meta": {k: v for k, v in (fin_meta or {}).items() if not isinstance(v, (list, dict))},
    }

    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    w = payload["windows"]["5y"] or {}
    log(f"записано {len(points)} точек ({points[0]['month']}…{points[-1]['month']}), валидных {len(valid)}; "
        f"P/E {current['pe']} · медиана 5л {w.get('median')} · перцентиль {w.get('percentile_of_current')} · "
        f"покрытие {current['coverage_pct']}% (сверено {current['verified_coverage_pct']}%)")
    return 0


def decompose(valid: list[dict]) -> dict | None:
    """Год к году: подешевел рынок из-за цен или из-за прибыли. P/E = Cap/E, поэтому
    изменение P/E раскладывается ровно на вклад капитализации и вклад прибыли."""
    if len(valid) < 13:
        return None
    now, prev = valid[-1], None
    target = shift(now["month"], 12)
    for p in valid:
        if p["month"] <= target:
            prev = p
    if not prev or prev["earnings"] <= 0 or prev["market_cap"] <= 0:
        return None
    return {
        "from": prev["month"], "to": now["month"],
        "pe_change_pct": round(100.0 * (now["pe"] / prev["pe"] - 1), 1),
        "cap_change_pct": round(100.0 * (now["market_cap"] / prev["market_cap"] - 1), 1),
        "earnings_change_pct": round(100.0 * (now["earnings"] / prev["earnings"] - 1), 1),
        "comparable_basket": now["constituents_used"] == prev["constituents_used"],
    }


def shift(month: str, back: int) -> str:
    t = int(month[:4]) * 12 + int(month[5:7]) - 1 - back
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def quantile(vals: list[float], q: float) -> float:
    pos = (len(vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def save_cache(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, CACHE)
    except OSError as exc:
        log(f"кэш не сохранён: {exc}")


def risk_free_pct():
    """Безрисковая ставка из существующего источника проекта (G-кривая MOEX)."""
    try:
        with open(os.path.join(ROOT, "site", "marlamov.json"), encoding="utf-8") as fh:
            rfr = (json.load(fh).get("meta") or {}).get("rfr")
        return round(float(rfr) * 100, 2) if isinstance(rfr, (int, float)) else None
    except (OSError, ValueError, TypeError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
