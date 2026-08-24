#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Квантовый скринер облигаций РФ (serverless, под статический сайт на GitHub Pages).

Источник — ТОЛЬКО реальный ISS MOEX. Синтетики нет. Пишет три JSON в site/bonds/:
  screener.json, chart_data.json, portfolios.json.

Корректировки относительно исходного ТЗ (по факту реального ISS, проверено):
  • G-Curve: MOEX отдаёт собственную модель (B1..B3,T1 + G1..G9), НЕ учебниковый NSS, и тут же —
    готовую крив亮 yearyields (zero-coupon yields по срокам). Дисконтируем по ней (интерполяция),
    а не переизобретаем проприетарную формулу с ошибкой.
  • Поля ISS реальные: купон COUPONPERCENT/COUPONVALUE/COUPONPERIOD(дни→частота),
    оферта OFFERDATE/PUTOPTIONDATE/CALLOPTIONDATE, дневной оборот в ₽ = VALTODAY,
    амортизация — из bondization (>1 строки amortizations).
  • Ежемесячный купон есть у рублёвых корпоратов (TQCB), у ОФЗ его нет → ОФЗ = линия кривой/бенчмарк.
  • Fair-value считаем для РУБЛЁВЫХ бумаг (рублёвая G-кривая). Замещающие (USD-номинал) не гоним
    через рублёвую кривую (нужна USD-кривая, MOEX её не публикует) — помечаем отдельно.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np
import requests
from scipy.optimize import brentq, linprog

try:
    from .official_ratings import DEFAULT_CACHE, load_official_ratings
except ImportError:  # direct script execution: python bonds/update_bonds.py
    from official_ratings import DEFAULT_CACHE, load_official_ratings

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO, "site", "bonds")
ISS = "https://iss.moex.com/iss"
UA = {"User-Agent": "dividend-site/bonds (+github.com/eremkindv91)", "Accept": "application/json"}
TODAY = date.today()

MIN_VALTODAY = 1_000_000.0            # дневной оборот, ₽ (реально VALTODAY)
TAX = 0.13                            # НДФЛ на купоны и доход
CORP_BOARD = "TQCB"                   # рублёвые корпораты (монтли-фильтр)
OFZ_BOARD = "TQOB"                    # ОФЗ — линия кривой/бенчмарк
PAGE = 100

# Спред к G-кривой по рейтинговому классу (годовые %).
SPREAD = {"AAA": 1.5, "AA": 2.2, "A": 3.5, "BBB": 5.0}
RATING_RANK = {  # для гейта «не ниже BBB-» и выбора спреда
    "AAA": 20, "AA+": 19, "AA": 18, "AA-": 17, "A+": 16, "A": 15, "A-": 14,
    "BBB+": 13, "BBB": 12, "BBB-": 11, "BB+": 10, "BB": 9, "BB-": 8,
    "B+": 7, "B": 6, "B-": 5, "CCC+": 4, "CCC": 3, "CCC-": 2, "CC": 1, "C": 0,
}
RATING_FLOOR = RATING_RANK["BBB-"]    # включительно

def http_json(url: str, retries: int = 4, timeout: int = 40) -> dict:
    last = None
    for a in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 ** a)
    raise RuntimeError(f"ISS недоступен: {url} :: {last}")


def block_rows(payload: dict, block: str) -> list[dict]:
    b = payload.get(block, {})
    cols = b.get("columns", [])
    return [dict(zip(cols, row)) for row in b.get("data", [])]


# ── G-Curve (реальная кривая MOEX) ───────────────────────────────────────────
def load_gcurve() -> tuple[np.ndarray, np.ndarray]:
    """Вернуть (сроки в годах, zero-rate в %) из опубликованной кривой MOEX yearyields."""
    d = http_json(f"{ISS}/engines/stock/zcyc.json?iss.meta=off")
    rows = block_rows(d, "yearyields")
    if not rows:
        raise RuntimeError("G-Curve: пустой yearyields — нет данных, СТОП")
    pts = sorted((float(r["period"]), float(r["value"])) for r in rows)
    t = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    return t, y


def gcurve_rate(t_years: float, gt: np.ndarray, gy: np.ndarray) -> float:
    """Zero-rate (%) на срок t лет: интерполяция кривой MOEX, плоская экстраполяция за краями."""
    t = max(t_years, 1e-6)
    return float(np.interp(t, gt, gy, left=gy[0], right=gy[-1]))


# ── Рейтинги ─────────────────────────────────────────────────────────────────
def norm(s: str) -> str:
    return (s or "").lower().replace("«", "").replace("»", "").replace('"', "").strip()


def spread_for(rating: str) -> float:
    grp = "AAA" if rating.startswith("AAA") else "AA" if rating.startswith("AA") \
        else "A" if rating.startswith("A") else "BBB"
    return SPREAD[grp]


# ── Загрузка борда (securities + marketdata, постранично) ─────────────────────
MIN_FORMED_SESSION = 50          # сколько ликвидных выпусков считать признаком набранной сессии
TURNOVER_BASIS: dict = {}        # борд → чем мерили оборот; уходит в meta, а не подразумевается


def last_session_turnover(board: str, back_days: int = 10) -> tuple[dict[str, float], str | None]:
    """Оборот за последний ЗАВЕРШЁННЫЙ торговый день борда: {SECID: ₽}.

    VALTODAY — оборот ТЕКУЩЕГО дня. До открытия и в первые часы сессии он нулевой почти у
    всех выпусков, поэтому фильтр ликвидности отбраковывает весь борд: запуск в 08:15 МСК
    дал 3 бумаги из 3016 и пустой скринер. История возвращает ту же величину, но
    детерминированно — состав выборки перестаёт зависеть от времени запуска.
    """
    for back in range(1, back_days + 1):
        day = TODAY - timedelta(days=back)
        rows: dict[str, float] = {}
        start = 0
        while True:
            d = http_json(f"{ISS}/history/engines/stock/markets/bonds/boards/{board}/securities.json"
                          f"?date={day.isoformat()}&iss.meta=off&iss.only=history"
                          f"&history.columns=SECID,VALUE&start={start}")
            page = block_rows(d, "history")
            if not page:
                break
            for r in page:
                value = r.get("VALUE")
                if value not in (None, ""):
                    rows[str(r["SECID"])] = float(value)
            start += len(page)
            if len(page) < PAGE:
                break
        if rows:                                   # выходной/праздник отдаёт пустую историю
            return rows, day.isoformat()
    return {}, None


def _normalize_turnover(board: str, rows: list[dict]) -> None:
    """Подменить недобранный внутридневной оборот оборотом последнего торгового дня.

    Делается в load_board, а не у конкретного потребителя: скринер и универсум обязаны
    мерить ликвидность одинаково, иначе они разойдутся составом на одном и том же прогоне.
    """
    formed = sum(1 for s in rows if float(s["_md"].get("VALTODAY") or 0) > MIN_VALTODAY)
    basis = {"source": "VALTODAY", "date": TODAY.isoformat(), "liquid_issues": formed}
    if formed < MIN_FORMED_SESSION:
        history, day = last_session_turnover(board)
        if history:
            for s in rows:
                # Оба поля: скринер читает VALTODAY, билдер универсума предпочитает
                # VALTODAY_RUR. Заменить одно — значит развести их составом на одном прогоне.
                value = history.get(s["SECID"], 0.0)
                s["_md"]["VALTODAY"] = value
                if "VALTODAY_RUR" in s["_md"]:
                    s["_md"]["VALTODAY_RUR"] = value
            formed = sum(1 for s in rows if float(s["_md"].get("VALTODAY") or 0) > MIN_VALTODAY)
            basis = {"source": "history", "date": day, "liquid_issues": formed}
            sys.stderr.write(f"[bonds] {board}: сессия не набрана — оборот за {day}, "
                             f"ликвидных выпусков {formed}\n")
        else:
            sys.stderr.write(f"[bonds] {board}: сессия не набрана, история пуста — по VALTODAY\n")
    TURNOVER_BASIS[board] = basis


def load_board(board: str) -> list[dict]:
    # ВАЖНО: securities.json борда облигаций отдаёт ВЕСЬ список разом и игнорирует start
    # (проверено: start=0 и start=100 → одни и те же бумаги). Поэтому выходим, как только
    # страница не добавила НИ ОДНОЙ новой бумаги (устойчиво и к реальной пагинации, если она появится).
    out: dict[str, dict] = {}
    start = 0
    while True:
        d = http_json(f"{ISS}/engines/stock/markets/bonds/boards/{board}/securities.json"
                      f"?iss.meta=off&start={start}")
        secs = block_rows(d, "securities")
        mkt = {m["SECID"]: m for m in block_rows(d, "marketdata")}
        if not secs:
            break
        new = 0
        for s in secs:
            sid = s["SECID"]
            if sid not in out:
                new += 1
            s["_md"] = mkt.get(sid, {})
            s["_board"] = board
            out[sid] = s
        if new == 0 or len(secs) < PAGE:
            break
        start += PAGE
    rows = list(out.values())
    _normalize_turnover(board, rows)
    return rows


def coupon_freq(coupon_period_days) -> int:
    try:
        d = float(coupon_period_days)
        return int(round(365.0 / d)) if d > 0 else 0
    except (TypeError, ValueError):
        return 0


def has_date(v) -> bool:
    """Дата «есть» только если это реальная дата (ISS кладёт плейсхолдер '0000-00-00' = пусто)."""
    return bool(v) and str(v) not in ("0000-00-00", "")


def cheap_pass(
    s: dict,
    monthly_only: bool,
    official_rating: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """Дешёвый фильтр по bulk-полям. Вернуть (прошёл, рейтинг, причина_отказа)."""
    md = s["_md"]
    valtoday = md.get("VALTODAY")
    if not valtoday or float(valtoday) <= MIN_VALTODAY:
        return False, None, "low_volume"
    # только РУБЛЁВЫЕ: на TQCB есть «Валютные облигации» (USD-номинал) — их нельзя
    # дисконтировать рублёвой G-кривой (нужна USD-кривая, MOEX её не даёт)
    if (s.get("FACEUNIT") or "").upper() not in ("SUR", "RUB", "RUR"):
        return False, None, "not_ruble"
    if "валют" in (s.get("BONDTYPE") or "").lower():
        return False, None, "fx_bond"
    if s.get("COUPONPERCENT") in (None, "", 0) or s.get("COUPONVALUE") in (None, ""):
        return False, None, "not_fixed"                      # флоатеры/без купона
    if float(s.get("COUPONPERCENT") or 0) < 1.0 or "иос" in norm(s.get("SHORTNAME", "")):
        return False, None, "structured_note"               # структурные ноты (Сбер ИОС и т.п.)
    # «без оферт» = без put/оферты И без call (чистый bullet → корректный fair-value к погашению)
    if any(has_date(s.get(k)) for k in ("OFFERDATE", "PUTOPTIONDATE", "CALLOPTIONDATE", "BUYBACKDATE")):
        return False, None, "has_offer_call"
    if not has_date(s.get("MATDATE")):
        return False, None, "no_maturity"
    freq = coupon_freq(s.get("COUPONPERIOD"))
    if monthly_only and freq != 12:
        return False, None, "not_monthly"
    rating = official_rating
    if not rating or RATING_RANK.get(rating, -99) < RATING_FLOOR:
        return False, rating, "rating_below_floor_or_unknown"
    return True, rating, None


# ── Денежные потоки из bondization ───────────────────────────────────────────
def load_cashflows(secid: str) -> tuple[list[tuple[date, float]], float, bool, bool]:
    """
    Вернуть (future_coupons[(date, rub)], face_value_rub, is_amortizing, has_offer).
    Амортизация = >1 строки в amortizations. Купоны — только будущие.
    """
    d = http_json(f"{ISS}/securities/{secid}/bondization.json?iss.meta=off&limit=unlimited")
    coupons = block_rows(d, "coupons")
    amorts = block_rows(d, "amortizations")
    offers = block_rows(d, "offers")
    is_amort = len([a for a in amorts if a.get("amortdate")]) > 1
    has_offer = len(offers) > 0
    fut: list[tuple[date, float]] = []
    for c in coupons:
        cd = c.get("coupondate")
        val = c.get("value")
        if not cd or val in (None, ""):
            continue
        try:
            dt = date.fromisoformat(cd)
        except ValueError:
            continue
        if dt > TODAY:
            fut.append((dt, float(val)))
    # номинал к погашению: последняя (конечная) строка amortizations = возврат тела
    face = None
    if amorts:
        last = sorted(amorts, key=lambda a: a.get("amortdate") or "")[-1]
        face = float(last.get("value") or last.get("facevalue") or 0) or None
    return fut, (face or 0.0), is_amort, has_offer


# ── ACT/ACT год-фракция + дисконт + YTM ──────────────────────────────────────
def yearfrac(d0: date, d1: date) -> float:
    return (d1 - d0).days / 365.0


def fair_value(cfs: list[tuple[date, float]], face: float, mat: date, accrued: float,
               spread: float, gt: np.ndarray, gy: np.ndarray) -> tuple[float, float, float]:
    """
    Вернуть (fair_clean_pct, fair_ytm_pct, fair_dirty_rub).
    Каждый CF дисконтируется по ставке gcurve(t)+spread (эфф. годовая), t = ACT/365.
    """
    flows = list(cfs)
    flows.append((mat, face))                                # возврат номинала
    flows = [(d, v) for d, v in flows if d > TODAY and v > 0]
    if not flows:
        raise ValueError("нет будущих потоков")
    dirty = 0.0
    for dt, v in flows:
        t = max(yearfrac(TODAY, dt), 1e-6)
        r = (gcurve_rate(t, gt, gy) + spread) / 100.0
        dirty += v / (1.0 + r) ** t
    fair_clean_pct = (dirty - accrued) / face * 100.0

    def npv(y):
        return sum(v / (1.0 + y) ** max(yearfrac(TODAY, dt), 1e-6) for dt, v in flows) - dirty
    try:
        ytm = brentq(npv, 1e-4, 3.0, maxiter=200)            # IRR при справедливой цене
    except ValueError:
        ytm = float("nan")
    return fair_clean_pct, ytm * 100.0, dirty


def market_clean(s: dict) -> float | None:
    # бумаги прошли VALTODAY>1M → торговались сегодня, WAPRICE (ср.взвеш. за день) надёжнее LAST/YIELD
    md = s["_md"]
    for k in ("WAPRICE", "MARKETPRICE", "LCLOSEPRICE", "MARKETPRICE2", "LCURRENTPRICE",
              "LAST", "PREVLEGALCLOSEPRICE"):
        v = md.get(k)
        if v not in (None, "", 0):
            return float(v)
    v = s.get("PREVPRICE")
    return float(v) if v not in (None, "", 0) else None


MIN_YTM, MAX_YTM = 5.0, 45.0             # sanity-гейт: вне диапазона → битая/устаревшая цена, дропаем


def solve_ytm(flows: list[tuple[date, float]], dirty_target: float) -> float:
    """IRR (%, эфф. годовая) набора потоков при заданной грязной цене. NaN если не решается."""
    f = [(d, v) for d, v in flows if d > TODAY and v > 0]
    if not f or dirty_target <= 0:
        return float("nan")

    def npv(y):
        return sum(v / (1.0 + y) ** max(yearfrac(TODAY, d), 1e-6) for d, v in f) - dirty_target
    try:
        if npv(1e-4) * npv(3.0) > 0:
            return float("nan")
        return brentq(npv, 1e-4, 3.0, maxiter=200) * 100.0
    except ValueError:
        return float("nan")


# ── Сборка скринера ──────────────────────────────────────────────────────────
TOP_N = 300                              # кап по ликвидности (топ по обороту) — bound на CI-время


def build_screener(gt: np.ndarray, gy: np.ndarray, ratings: dict[str, dict]) -> list[dict]:
    raw = load_board(CORP_BOARD)             # оборот уже нормализован (см. _normalize_turnover)
    sys.stderr.write(f"[bonds] {CORP_BOARD}: загружено {len(raw)} бумаг\n")
    survivors = []
    for s in raw:
        rating_record = ratings.get(str(s.get("ISIN") or s["SECID"]).upper())
        rating = rating_record.get("rating") if rating_record else None
        # Частота купона больше НЕ фильтр: раньше стояло monthly_only=True, и скринер
        # показывал только ежемесячные выпуски — 67 бумаг из тысячи. Частота выплат это
        # предпочтение инвестора, а не признак качества, и выбирается фильтром в интерфейсе.
        ok, rating, _ = cheap_pass(s, monthly_only=False, official_rating=rating)
        if ok:
            s["_rating"] = rating
            s["_rating_record"] = rating_record
            survivors.append(s)
    survivors.sort(key=lambda s: -float(s["_md"].get("VALTODAY") or 0))
    survivors = survivors[:TOP_N]
    sys.stderr.write(f"[bonds] прошли дешёвый фильтр (топ-{TOP_N} по обороту): {len(survivors)} → bondization параллельно\n")

    def fetch(s):
        try:
            return load_cashflows(s["SECID"])
        except Exception:  # noqa: BLE001
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        cf_res = list(ex.map(fetch, survivors))

    out = []
    for s, res in zip(survivors, cf_res):
        sid = s["SECID"]
        if res is None:
            continue
        cfs, face, is_amort, has_offer = res
        if is_amort or has_offer or not cfs or face <= 0:
            continue
        try:
            mat = date.fromisoformat(s["MATDATE"])
        except ValueError:
            continue
        mclean = market_clean(s)
        if mclean is None:
            continue
        accrued = float(s.get("ACCRUEDINT") or 0.0)
        rating = s["_rating"]
        try:
            fclean, fytm, _ = fair_value(cfs, face, mat, accrued, spread_for(rating), gt, gy)
        except ValueError:
            continue
        if not np.isfinite(fytm):
            continue
        md = s["_md"]
        rating_record = s["_rating_record"]
        # market/net YTM считаем САМИ из WAPRICE+реальных потоков (поле YIELD у MOEX бывает устаревшим)
        gross_flows = list(cfs) + [(mat, face)]
        mkt_dirty = mclean / 100.0 * face + accrued
        mkt_ytm = solve_ytm(gross_flows, mkt_dirty)
        if not (MIN_YTM <= mkt_ytm <= MAX_YTM):              # вне диапазона → битая цена, дроп
            continue
        gain_rub = max(0.0, (100.0 - mclean) / 100.0 * face)  # ценовой доход к погашению
        net_flows = [(d, v * (1.0 - TAX)) for d, v in cfs]
        net_flows.append((mat, face - gain_rub * TAX))
        net_ytm = solve_ytm(net_flows, mkt_dirty)
        if not np.isfinite(net_ytm):
            net_ytm = mkt_ytm * (1.0 - TAX)
        dur_days = md.get("DURATION")
        dur_years = (float(dur_days) / 365.0) if dur_days not in (None, "", 0) else yearfrac(TODAY, mat)
        deviation = fclean / mclean - 1.0                    # апсайд цены (fair > market → недооценён)
        coup_pct = float(s.get("COUPONPERCENT") or 0.0)
        out.append({
            "secid": sid, "isin": s.get("ISIN"), "name": s.get("SHORTNAME"),
            "board": s["_board"], "rating": rating, "rating_group": spread_for_group(rating),
            "rating_source": "official_issue",
            "rating_agency": rating_record.get("rating_agency"),
            "rating_date": rating_record.get("rating_date"),
            "rating_checked_at": rating_record.get("rating_checked_at"),
            "rating_source_url": rating_record.get("rating_source_url"),
            "rating_records": rating_record.get("rating_records", []),
            "currency": s.get("FACEUNIT"), "face": face,
            "price_market": round(mclean, 3),
            "ytm_market": round(mkt_ytm, 3),
            "ytm_net": round(net_ytm, 3),
            "price_fair": round(fclean, 3), "ytm_fair": round(fytm, 3),
            "deviation": round(deviation * 100, 2),
            "duration_years": round(dur_years, 2),
            "coupon_pct": round(coup_pct, 2),
            "freq": coupon_freq(s.get("COUPONPERIOD")),
            "maturity": s.get("MATDATE"),
            "valtoday": round(float(md.get("VALTODAY")), 0),
            "lot_value": float(s.get("LOTVALUE") or s.get("FACEVALUE") or face),
            "max_rub": round(0.05 * float(md.get("VALTODAY")), 0),   # ≤5% дневного оборота
        })
    sys.stderr.write(f"[bonds] итог скринера: {len(out)} бумаг\n")
    return out


def spread_for_group(rating: str) -> str:
    return "AAA" if rating.startswith("AAA") else "AA" if rating.startswith("AA") \
        else "A" if rating.startswith("A") else "BBB"


# ── Оптимизация портфелей (linprog) ──────────────────────────────────────────
BUCKETS = {"short": (0.0, 1.0), "mid": (1.0, 3.0), "long": (3.0, 30.0)}


def optimize_bucket(bonds: list[dict], lo: float, hi: float) -> dict | None:
    cand = [b for b in bonds if lo <= b["duration_years"] < hi and b["ytm_net"] and b["ytm_net"] > 0]
    if len(cand) < 2:
        return None
    n = len(cand)
    c = [-b["ytm_net"] for b in cand]                        # max чистой YTM
    dur = [b["duration_years"] for b in cand]
    target = (lo + hi) / 2 if hi < 30 else max(3.5, min(d for d in dur))
    # дюрация портфеля в коридоре [lo, hi]; сумма весов = 1; вес эмитента ≤ 15%
    A_ub = [dur, [-d for d in dur]]
    b_ub = [hi, -lo]
    res = linprog(c=c, A_ub=A_ub, b_ub=b_ub, A_eq=[[1] * n], b_eq=[1],
                  bounds=[(0, 0.15)] * n, method="highs")
    if not res.success:
        return None
    w = res.x
    picks = [(cand[i], float(w[i])) for i in range(n) if w[i] > 0.005]
    picks.sort(key=lambda x: -x[1])
    port_ytm = sum(b["ytm_net"] * wt for b, wt in picks)
    port_dur = sum(b["duration_years"] * wt for b, wt in picks)
    return {
        "target_duration": round(target, 2),
        "port_ytm_net": round(port_ytm, 3),
        "port_duration": round(port_dur, 2),
        "bonds": [{
            "secid": b["secid"], "name": b["name"], "rating": b["rating"],
            "rating_agency": b.get("rating_agency"), "rating_date": b.get("rating_date"),
            "rating_source_url": b.get("rating_source_url"),
            "weight": round(wt, 4), "ytm_net": b["ytm_net"], "duration_years": b["duration_years"],
            "price_market": b["price_market"], "lot_value": b["lot_value"],
            "max_rub": b["max_rub"], "coupon_pct": b["coupon_pct"], "maturity": b["maturity"],
        } for b, wt in picks],
    }


def build_portfolios(bonds: list[dict]) -> dict:
    return {name: optimize_bucket(bonds, lo, hi) for name, (lo, hi) in BUCKETS.items()}


# ── Кривая для графика (ОФЗ-линия) + scatter корпоратов ──────────────────────
def build_chart(gt: np.ndarray, gy: np.ndarray, bonds: list[dict]) -> dict:
    ofz_curve = [{"t": round(float(t), 3), "yield": round(float(y), 3)} for t, y in zip(gt, gy)]
    corp = [{"secid": b["secid"], "name": b["name"], "rating": b["rating"],
             "rating_agency": b.get("rating_agency"), "rating_date": b.get("rating_date"),
             "rating_source_url": b.get("rating_source_url"),
             "duration": b["duration_years"], "ytm": b["ytm_market"], "ytm_fair": b["ytm_fair"],
             "deviation": b["deviation"]} for b in bonds if b["ytm_market"]]
    return {"ofz_curve": ofz_curve, "corp_points": corp,
            "spread": SPREAD, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        gt, gy = load_gcurve()
        ratings, ratings_meta = load_official_ratings(cache_path=DEFAULT_CACHE, refresh=True)
        if ratings_meta.get("sources_ok", 0) == 0:
            raise RuntimeError("все официальные источники рейтингов недоступны")
        screener = build_screener(gt, gy, ratings)
        if not screener:
            sys.stderr.write("[bonds] СТОП: скринер пуст — не публикуем\n")
            return 1
        portfolios = build_portfolios(screener)
        chart = build_chart(gt, gy, screener)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[bonds] СТОП, не публикуем: {e}\n")
        return 1
    meta = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data_date": str(TODAY), "n": len(screener),
            "spread": SPREAD,
            # За какой день мерили оборот на каждом борде. Если сборку запустили до набора
            # сессии, это НЕ сегодня — умолчать значило бы выдать вчерашнюю ликвидность за текущую.
            "turnover_basis": dict(TURNOVER_BASIS),
            "ratings": ratings_meta,
            "rating_coverage": {
                "official_issue_ratings": len(screener),
                "policy": "minimum current issue rating across official agencies",
            },
            "note": "Скринер: рублёвые корпораты TQCB, фиксированный купон ЛЮБОЙ частоты "
                    "(ежемесячный, квартальный, полугодовой, годовой — колонка «Выплат/год»), "
                    "без оферт/амортизации/"
                    "структурных нот, официальный рейтинг выпуска ≥ BBB-. Рейтинги ежедневно проверяются "
                    "по АКРА, Эксперт РА и НКР; при нескольких оценках используется минимальная. "
                    "Fair-value = дисконт реальных потоков по G-кривой "
                    "MOEX + ПЛОСКИЙ спред рейтинга. ВАЖНО: плоский спред занижает реальную кредит-премию "
                    "имён A-/BBB → большой положительный «апсайд» у них = модельное допущение, а не "
                    "гарантированная недооценка. Портфели максимизируют ЧИСТУЮ YTM (не risk-adjusted): "
                    "контроль риска — только рейтинг ≥ BBB- и кап эмитента 15%. Не ИИР."}
    for fname, payload in (("screener.json", {"meta": meta, "bonds": screener}),
                           ("chart_data.json", chart),
                           ("portfolios.json", {"meta": meta, "portfolios": portfolios})):
        with open(os.path.join(OUT_DIR, fname), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    # Bond Portfolio Lab v3 is additive during migration. Its quality gate owns
    # publication of the normalized universe and preset matrix; a failed v3 run
    # never deletes the previous valid composition and does not corrupt the
    # backwards-compatible screener/Finder artifacts above.
    try:
        if REPO not in sys.path:
            sys.path.insert(0, REPO)
        from bonds.pipeline_v3 import build_and_publish

        v3_validation = build_and_publish(
            load_board=load_board,
            http_json=http_json,
            iss=ISS,
            ratings=ratings,
            ratings_meta=ratings_meta,
            gcurve_rate=lambda years: gcurve_rate(years, gt, gy),
            curve_points=[(float(t), float(y)) for t, y in zip(gt, gy)],
        )
        sys.stderr.write(f"[bonds-v3] status={v3_validation.get('status')}\n")
    except Exception as exc:  # noqa: BLE001
        # Preserve the last valid v3 artifacts. The legacy update remains useful,
        # while the workflow log records the v3 execution failure explicitly.
        sys.stderr.write(f"[bonds-v3] EXECUTION_FAILED, previous valid artifacts kept: {exc}\n")
    sys.stderr.write(f"[bonds] OK → {OUT_DIR} (screener={len(screener)})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
