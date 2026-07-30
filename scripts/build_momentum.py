#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Месячная история MOEX → model_output/momentum.json (скаляры) + model_output/returns.json (ряд).

Из месячных свечей (~7.6 года, interval=31) считаем:
  • momentum.json: WML 12-1 (Jegadeesh-Titman, как в нб 01) + годовая волатильность — для скоринга
    и inverse-vol взвешивания корзины (ребаланс momentum месячный → пересчитываем раз в месяц);
  • returns.json: ряд МЕСЯЧНЫХ доходностей по тикерам, выровненный на общую ось месяцев — фронт
    собирает из него ряд доходностей корзины и считает КОРРЕКТНЫЕ риск-метрики (Sharpe/Sortino/
    Calmar/maxDD/волатильность с учётом корреляций) + основа для ковариации (Фаза 3, оптимизатор).

CIRCUIT-BREAKER против троттлинга MOEX с облачных IP (как smartlab_reconcile): CB_MAX сетевых
ошибок ПОДРЯД → стоп, union с прошлым (ничего не теряем). Запускать ЛУЧШЕ ЛОКАЛЬНО + коммитить.

Запуск:  python scripts/build_momentum.py
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
from moex_iss import fetch_candles, fetch_dividends  # noqa: E402

ARTIFACT = os.path.join(REPO, "model_output", "forecast_rf.json")
SUPPLEMENT = os.path.join(REPO, "data", "supplementary_universe.json")
OUT_MOM = os.path.join(REPO, "model_output", "momentum.json")
OUT_RET = os.path.join(REPO, "model_output", "returns.json")
CB_MAX = 8                 # сетевых ошибок подряд → circuit-breaker
PAUSE = 0.15
HISTORY_DAYS = 2780        # ~7.6 года месячных свечей
MAX_MONTHS = 96            # ось ряда доходностей (8 лет)
VOL_WINDOW = 60            # окно для скалярной vol_ann (5 лет)


LINEAGE = os.path.join(REPO, "data", "corporate_lineage.json")
LINEAGE_MAX_GAP = 0            # дыра на МЕСЯЧНОЙ сетке
LINEAGE_MAX_JUNCTION = 0.35    # |доходность стыка| выше → похоже на нераспознанный сплит


def load_lineages() -> dict:
    """successor → запись правопреемства. Ничего не выводится из похожести названий."""
    try:
        with open(LINEAGE, encoding="utf-8") as fh:
            rows = json.load(fh).get("lineages", [])
    except (OSError, ValueError) as e:
        sys.stderr.write(f"[momentum] правопреемства не прочитаны ({e})\n")
        return {}
    return {str(r["successor"]).upper(): r for r in rows
            if r.get("successor") and r.get("predecessor")}


def apply_lineage(successor: str, rec: dict, candles: list) -> tuple[list, dict]:
    """Продлить ряд действующей бумаги рядом предшественника — если склейка доказуема.

    Гейты (любой не пройден → склейки НЕТ, причина возвращается):
      1) у предшественника есть месячные свечи;
      2) на МЕСЯЧНОЙ сетке между последним месяцем предшественника и первым месяцем
         преемника нет пропущенных месяцев. Пропуск нельзя ни занулить (фиктивная
         нулевая волатильность), ни слепить в один прыжок (фиктивная доходность —
         именно так FIVE→X5 дал бы +15,5% за один месяц вместо 8 неторговых);
      3) доходность стыка правдоподобна (|r| ≤ 35%) — иначе это скорее нераспознанный
         сплит/деноминация, чем рыночное движение при конверсии 1:1;
      4) месяцы не перекрываются (двойной учёт одного периода).
    Коэффициент конверсии применяется к ценам предшественника, если он не 1:1.
    """
    pred = str(rec["predecessor"]).upper()
    ratio = float(rec.get("ratio") or 1.0)
    base = {"successor": successor, "predecessor": pred, "ratio": ratio,
            "junction": rec.get("junction"), "kind": rec.get("kind"),
            "evidence": rec.get("evidence"), "applied": False}

    if not candles:
        return candles, {**base, "status": "rejected", "reason": "у преемника нет свечей"}
    try:
        pred_candles = fetch_candles(pred, days=HISTORY_DAYS * 2, interval=31)
    except Exception as e:  # noqa: BLE001
        return candles, {**base, "status": "rejected", "reason": f"история предшественника не получена: {e}"}
    if not pred_candles:
        return candles, {**base, "status": "rejected", "reason": "у предшественника нет свечей"}

    pred_months = [d[:7] for d, _, _ in pred_candles]
    succ_months = [d[:7] for d, _, _ in candles]
    overlap = sorted(set(pred_months) & set(succ_months))
    if overlap:
        return candles, {**base, "status": "rejected",
                         "reason": f"месяцы перекрываются ({overlap[0]}…{overlap[-1]}) — двойной учёт"}

    ly, lm = map(int, pred_months[-1].split("-"))
    fy, fm = map(int, succ_months[0].split("-"))
    gap = (fy - ly) * 12 + (fm - lm) - 1
    if gap > LINEAGE_MAX_GAP:
        return candles, {**base, "status": "rejected", "gap_months": gap,
                         "reason": (f"дыра {gap} мес. между {pred_months[-1]} и {succ_months[0]}: "
                                    "пропуск нельзя ни занулить, ни слепить в один месяц")}

    last_pred_close = pred_candles[-1][1] * ratio
    first_succ_close = candles[0][1]
    junction = first_succ_close / last_pred_close - 1.0 if last_pred_close else None
    if junction is None or abs(junction) > LINEAGE_MAX_JUNCTION:
        return candles, {**base, "status": "rejected", "junction_return": junction,
                         "reason": (f"доходность стыка {junction:+.2%} выше порога "
                                    f"{LINEAGE_MAX_JUNCTION:.0%} — похоже на нераспознанный сплит"
                                    if junction is not None else "цена стыка недоступна")}

    stitched = [(d, c * ratio, v) for d, c, v in pred_candles] + list(candles)
    return stitched, {**base, "status": "applied", "applied": True, "gap_months": gap,
                      "junction_return": round(junction, 6),
                      "months_added": len(pred_candles), "months_total": len(stitched),
                      "reason": (f"продлено на {len(pred_candles)} мес. предшественника; "
                                 f"стык {junction:+.2%}, дыры нет")}


def main() -> int:
    art = json.load(open(ARTIFACT, encoding="utf-8"))
    tickers = [r["ticker"] for r in art["tickers"]]
    # Универсум истории БОЛЬШЕ, чем универсум модели. Раньше он совпадал с ML-артефактом,
    # поэтому паи БПИФ, SNGS/SNGSP и недавно размещённые бумаги не могли получить ряд
    # доходностей ни при какой доске и навсегда выпадали из риск-метрик X-Ray и
    # оптимизатора. Список — data/supplementary_universe.json (явный и небольшой:
    # тянуть все ~500 бумаг TQBR раздуло бы returns.json без пользы).
    instrument_types: dict = {}
    extra: list[str] = []
    if os.path.exists(SUPPLEMENT):
        try:
            supp = json.load(open(SUPPLEMENT, encoding="utf-8"))
            known = set(tickers)
            extra = [str(x["secid"]).upper() for x in supp.get("instruments", [])
                     if x.get("secid") and str(x["secid"]).upper() not in known]
        except (OSError, ValueError, KeyError) as e:
            sys.stderr.write(f"[momentum] дополнительный универсум не прочитан ({e}) — только ML-список\n")
    if extra:
        # Тип и доску НЕ предполагаем — спрашиваем у ISS; неторгуемое не тянем.
        try:
            sys.path.insert(0, os.path.join(REPO, "scripts"))
            import moex_instruments as mi  # noqa: E402
            resolved = mi.describe_many(extra)
            keep = []
            for tk, info in resolved.items():
                if not info.get("found"):
                    sys.stderr.write(f"[momentum] {tk}: {info.get('reason')} — пропуск\n")
                    continue
                if not info.get("is_traded"):
                    sys.stderr.write(f"[momentum] {tk}: торги прекращены — пропуск\n")
                    continue
                instrument_types[tk] = info.get("instrument_type")
                keep.append(tk)
            extra = keep
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[momentum] discovery недоступен ({e}) — берём список как есть\n")
        tickers = tickers + extra
        sys.stderr.write(f"[momentum] универсум: {len(tickers)} (ML {len(tickers) - len(extra)} + вне модели {len(extra)})\n")
    prev_mom = {}
    if os.path.exists(OUT_MOM):
        try:
            prev_mom = json.load(open(OUT_MOM, encoding="utf-8")).get("data", {})
        except Exception:  # noqa: BLE001
            prev_mom = {}
    mom_out = dict(prev_mom)
    series: dict = {}          # tk → {month 'YYYY-MM': ценовая доходность}
    div_series: dict = {}      # tk → {month: реализованная дивдоходность (дивиденд_месяца / цена_пред_месяца)}

    consec_err = n_ok = n_skip = n_err = 0
    tripped = False
    lineages, lineage_log = load_lineages(), {}
    for tk in tickers:
        try:
            candles = fetch_candles(tk, days=HISTORY_DAYS, interval=31)   # месячные
            # Корпоративное правопреемство: ряд предшественника продлевает ряд действующей
            # бумаги, но ТОЛЬКО если склейка проходит гейты (см. data/corporate_lineage.json).
            if tk in lineages:
                candles, note = apply_lineage(tk, lineages[tk], candles)
                lineage_log[tk] = note
                sys.stderr.write(f"[momentum] lineage {tk}: {note['status']} — {note['reason']}\n")
            consec_err = 0
        except Exception as e:  # noqa: BLE001
            consec_err += 1
            n_err += 1
            sys.stderr.write(f"[momentum] {tk}: {e}\n")
            if consec_err >= CB_MAX:
                sys.stderr.write(f"[momentum] ⚡ CIRCUIT-BREAKER: {CB_MAX} ошибок подряд → стоп "
                                 f"(сохраняю {n_ok} свежих + прошлые)\n")
                tripped = True
                break
            time.sleep(1.0)
            continue
        if len(candles) < 13:
            n_skip += 1
            continue
        months = [d[:7] for d, _, _ in candles]
        closes = [c for _, c, _ in candles]
        values = [v for _, _, v in candles]      # месячный оборот, ₽
        # WML 12-1: последний ПОЛНЫЙ месяц [-2] к 12 мес. назад [-13] (скип текущего [-1])
        mom = (closes[-2] / closes[-13] - 1) if closes[-13] > 0 else None
        # месячные доходности (ключ = месяц закрытия)
        rets = {}
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                rets[months[i]] = closes[i] / closes[i - 1] - 1
        rvals = list(rets.values())
        vol = (statistics.pstdev(rvals[-VOL_WINDOW:]) * math.sqrt(12)) if len(rvals) >= 12 else None
        recent_val = [v for v in values[-VOL_WINDOW:] if v > 0]
        adv = round(statistics.mean(recent_val) / 21) if recent_val else None   # ADV: среднедневной оборот ₽ (≈21 торг.дней/мес)
        mom_out[tk] = {"mom": round(mom, 4) if mom is not None else None,
                       "vol_ann": round(vol, 4) if vol is not None else None, "adv": adv}
        series[tk] = rets
        # реальные дивиденды → месячная реализованная дивдоходность (дивиденд_месяца / цена пред. месяца)
        try:
            divs = fetch_dividends(tk)
        except Exception:  # noqa: BLE001
            divs = []
        div_by_month = {}
        for dt, val in divs:
            div_by_month[dt[:7]] = div_by_month.get(dt[:7], 0.0) + val
        close_by_month = dict(zip(months, closes))
        dy, prev_m = {}, None
        for mth in months:
            if prev_m and div_by_month.get(mth, 0) > 0 and close_by_month.get(prev_m, 0) > 0:
                dy[mth] = div_by_month[mth] / close_by_month[prev_m]
            prev_m = mth
        div_series[tk] = dy
        n_ok += 1
        time.sleep(PAUSE)

    # ── ось месяцев (последние MAX_MONTHS) + выравнивание рядов ──
    all_months = sorted({m for s in series.values() for m in s})
    axis = all_months[-MAX_MONTHS:]
    ret_data = {tk: [round(s[m], 4) if m in s else None for m in axis] for tk, s in series.items()}
    div_data = {tk: [round(s.get(m, 0.0), 5) for m in axis] for tk, s in div_series.items() if any(s.values())}

    asof = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    json.dump({"meta": {"asof": asof, "n": len(mom_out), "n_fresh": n_ok, "n_err": n_err,
                        "tripped": tripped, "source": "MOEX ISS monthly candles (TQBR), WML 12-1 + vol_ann"},
               "data": mom_out}, open(OUT_MOM, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"meta": {"asof": asof, "months": axis, "n_tickers": len(ret_data),
                        # Провенанс склеек: и применённых, и ОТВЕРГНУТЫХ гейтом. Отказ должен
                        # быть виден в данных, иначе «почему у X5 всего 19 месяцев» не проверить.
                        "lineage": lineage_log,
                        "note": "месячные ценовые доходности + реальная дивдоходность (блок div)"},
               "data": ret_data, "div": div_data,
               # тип инструмента для бумаг вне ML-модели: фронт по нему отличает пай БПИФ
               # от акции (сектор фонду не присваивается, sector cap к нему не применяется)
               "instrument_types": instrument_types}, open(OUT_RET, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[momentum] momentum.json: {len(mom_out)} | returns.json: {len(ret_data)} тикеров × "
          f"{len(axis)} мес ({axis[0] if axis else '—'}…{axis[-1] if axis else '—'}) | "
          f"свежих {n_ok} | ошибок {n_err} | breaker={'ДА' if tripped else 'нет'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
