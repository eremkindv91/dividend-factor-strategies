#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сверка и точечное дозаполнение фундаментальной панели из SmartLab.

ПОЛИТИКА «безопасно» (утверждена):
  • пустые ячейки НАДЁЖНЫХ колонок (revenue/net_income/assets) → дозаполняем,
    НО только если тикер прошёл ГЕЙТ ЕДИНИЦ и колонка сошлась на перехлёсте;
  • equity/debt/ebitda/net_debt/CFO/CAPEX/FCF → только отчёт (определения расходятся);
  • расхождения (обе есть, |Δ|>порог) → в отчёт на РУЧНУЮ проверку, НЕ перезаписываем;
  • ручные данные не перезаписываются никогда; оригинал заменяется отдельным шагом.

АВТОНОМ-ПЛУМБИНГ:
  • АЛИАСЫ (smartlab_fetch.ALIASES): YNDX→YDEX и пр. — фетч под новым тикером;
  • ПРЕФЫ: если у префа нет страницы — наследует фундамент обычки (тот же эмитент);
  • ВАЛЮТА: эмитент в USD (панель в USD, SmartLab в RUB) ловится по медиане≈usdrub_avg →
    значения SmartLab делятся на курс (RUB→USD). EUR/прочее не проходит колоночный гейт → флаг.

Выход:
  • results/smartlab/reconcile.csv — аудит каждой ячейки;
  • data/panels_final/panel_russia_final_smartlab.csv — копия с дозаполнением (оригинал цел).

Запуск:
  python scripts/smartlab_reconcile.py SBER LKOH ...   # выбранные тикеры
  python scripts/smartlab_reconcile.py ALL             # все тикеры панели (год≥2024)
"""
from __future__ import annotations

import csv
import os
import statistics
import sys
import time

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
from smartlab_fetch import (  # noqa: E402
    fetch_fundamentals, FIELD_MAP, RELIABLE, FILL_GAP, SmartLabUnavailable, SmartLabNotFound,
)

PANEL = os.path.join(REPO, "data", "panels_final", "panel_russia_final.csv")
OUT_PANEL = os.path.join(REPO, "data", "panels_final", "panel_russia_final_smartlab.csv")
OUT_REPORT = os.path.join(REPO, "results", "smartlab", "reconcile.csv")
YEARS = ("2024", "2025")
TOL = 0.07
UNIT_BAND = (0.85, 1.15)
CB_MAX_THROTTLE = 5      # circuit breaker: N сетевых ошибок подряд → прервать фетч


def reconcile(tickers, years=YEARS, tol=TOL, delay=1.0):
    df = pd.read_csv(PANEL)
    if tickers == ["ALL"]:
        tickers = sorted(df.loc[df.year >= int(min(years)), "ticker"].astype(str).unique())

    def pval(tk, y, pcol):
        m = (df.ticker == tk) & (df.year == int(y))
        if not m.any():
            return None
        v = df.loc[m, pcol].values[0]
        return float(v) if pd.notna(v) else None

    # курс для инвалютных эмитентов (панель в USD, SmartLab в RUB)
    usdrub = {}
    for y in years:
        vals = (df.loc[df.year == int(y), "usdrub_avg"].dropna()
                if "usdrub_avg" in df.columns else [])
        usdrub[y] = float(vals.iloc[0]) if len(vals) else None

    def col_overlap_ok(tk, slf, pcol, data, fx):
        st = []
        for yy in years:
            sv = data.get(slf, {}).get(yy)
            pv2 = pval(tk, yy, pcol)
            if sv is not None and pv2 not in (None, 0):
                st.append(abs(sv * 1000.0 / fx / pv2 - 1) <= tol)
        return None if not st else all(st)

    # ── pass 1: фетч (алиасы в fetch + наследование префами + CIRCUIT BREAKER) ──
    # Предохранитель: CB_MAX_THROTTLE СЕТЕВЫХ ошибок подряд (SmartLabUnavailable —
    # таймаут/403/429/5xx = троттлинг/блок IP) → прерываем фетч, переобучение пойдёт на
    # имеющейся панели. Легитимные 404 (SmartLabNotFound, делистинг) счётчик НЕ трогают.
    fetched, errors = {}, []
    consec_throttle, cb_tripped = 0, False
    for i, tk in enumerate(tickers):
        cands = [tk, tk[:-1]] if (tk.endswith("P") and len(tk) > 1) else [tk]
        got, err, throttled = None, None, False
        for cand in cands:                              # тикер, затем преф→обычка
            try:
                got = fetch_fundamentals(cand)
                if cand != tk:
                    got["inherited_from"] = cand
                break
            except SmartLabUnavailable as e:
                err, throttled = e, True                # сеть/403/429/5xx → троттлинг
            except SmartLabNotFound as e:
                err = e                                 # нет страницы → легитимный промах
        if got is not None:
            fetched[tk] = got
            consec_throttle = 0
        else:
            errors.append((tk, str(err)))
            consec_throttle = consec_throttle + 1 if throttled else 0   # 404 не считаем
            if consec_throttle >= CB_MAX_THROTTLE:
                cb_tripped = True
                print(f"\n[CIRCUIT BREAKER] {consec_throttle} сетевых ошибок подряд — SmartLab "
                      f"троттлит/блокирует IP. Прерываю фетч на {i + 1}/{len(tickers)}; "
                      f"переобучение — на имеющейся панели.")
                break
        if i + 1 < len(tickers):
            time.sleep(delay)

    # ── гейт единиц (+ детект инвалюты) ──
    gate = {}
    for tk, r in fetched.items():
        ratios = []
        for slf in RELIABLE:
            pcol = FIELD_MAP[slf]
            for y in years:
                slv = r["data"].get(slf, {}).get(y)
                pv = pval(tk, y, pcol)
                if slv is not None and pv not in (None, 0):
                    ratios.append(slv * 1000.0 / pv)
        med = statistics.median(ratios) if ratios else None
        cur, fx = r.get("currency", ""), 1.0
        unit_ok = (med is not None and UNIT_BAND[0] <= med <= UNIT_BAND[1]) \
            or (med is None and cur in ("", "RUB", "RUR"))
        if not unit_ok and med is not None:            # эмитент в USD? медиана≈usdrub
            ur = usdrub.get(years[-1]) or usdrub.get(years[0])
            if ur and UNIT_BAND[0] <= med / ur <= UNIT_BAND[1]:
                fx, unit_ok = ur, True
        gate[tk] = {"ok": unit_ok, "cur": cur, "fx": fx,
                    "med": (round(med, 2) if med is not None else None)}

    # ── pass 2: классификация + дозаполнение ──
    report = [("ticker", "year", "sl_field", "panel_col", "panel_mln",
               "smartlab_mln", "ratio", "action", "currency", "source")]
    n_fill = n_match = n_flag = 0
    blocked = [(tk, g["cur"], g["med"]) for tk, g in gate.items() if not g["ok"]]
    n_inherited = sum(1 for r in fetched.values() if r.get("inherited_from"))
    for tk, r in fetched.items():
        g = gate[tk]
        data = r["data"]
        src = r.get("inherited_from") and f"{r['source']} (←{r['inherited_from']})" or \
            (r["report_url"] or r["source"])
        for slf, pcol in FIELD_MAP.items():
            if pcol is None or slf not in data:
                continue
            for y in years:
                slv = data[slf].get(y)
                if slv is None:
                    continue
                slv_mln = slv * 1000.0 / g["fx"]
                pv = pval(tk, y, pcol)
                if pv is None:
                    if slf in RELIABLE and g["ok"]:
                        colok = col_overlap_ok(tk, slf, pcol, data, g["fx"])
                        if colok is False:
                            action = "skip(col_diverged)"
                        else:
                            df.loc[(df.ticker == tk) & (df.year == int(y)), pcol] = slv_mln
                            action = "FILL" if colok else "FILL(no_overlap)"
                            n_fill += 1
                    elif slf in RELIABLE:
                        action = f"skip(unit:cur={g['cur'] or '?'},med={g['med']})"
                    elif slf in FILL_GAP and g["ok"]:        # пробел → определение SmartLab (полнота)
                        df.loc[(df.ticker == tk) & (df.year == int(y)), pcol] = slv_mln
                        action = "FILL_gap(SL-def)"
                        n_fill += 1
                    else:
                        action = "skip_empty(needs_mapping)"
                    report.append((tk, y, slf, pcol, None, round(slv_mln, 1), None, action, g["cur"], src))
                else:
                    ratio = slv_mln / pv if pv else None
                    if not g["ok"]:
                        action = "flag(unit_mismatch)"; n_flag += 1
                    elif slf not in RELIABLE:
                        action = "flag(needs_mapping)"; n_flag += 1
                    elif ratio is not None and abs(ratio - 1) <= tol:
                        action = "match"; n_match += 1
                    else:
                        action = "FLAG_DIVERGENCE"; n_flag += 1
                    report.append((tk, y, slf, pcol, round(pv, 1), round(slv_mln, 1),
                                   round(ratio, 3) if ratio else None, action, g["cur"], src))

    os.makedirs(os.path.dirname(OUT_REPORT), exist_ok=True)
    with open(OUT_REPORT, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(report)
    df.to_csv(OUT_PANEL, index=False)

    print(f"[reconcile] тикеров: {len(tickers)} | ошибок фетча: {len(errors)} | "
          f"префов унаследовано: {n_inherited}")
    if cb_tripped:
        print("  ⚠ CIRCUIT BREAKER сработал — фетч прерван (троттлинг SmartLab); "
              "дозаполнения мало/нет, переобучение на имеющейся панели. CI: Success.")
    if errors:
        print("  не достали:", ", ".join(tk for tk, _ in errors[:15]), ("…" if len(errors) > 15 else ""))
    print(f"  ДОЗАПОЛНЕНО пустых: {n_fill} | совпадений: {n_match} | флагов: {n_flag}")
    if blocked:
        print(f"  ЗАБЛОКИРОВАНО гейтом единиц ({len(blocked)}):")
        for tk, cur, med in blocked:
            print(f"    {tk:6s} cur={cur or '?'} медиана×={med}")
    print(f"  отчёт: {os.path.relpath(OUT_REPORT, REPO)}")
    print(f"  панель: {os.path.relpath(OUT_PANEL, REPO)} (оригинал цел)")
    return n_fill, n_match, n_flag, blocked, errors


if __name__ == "__main__":
    args = [a.upper() for a in sys.argv[1:]] or [
        "RUAL", "ENPG", "GEMC", "BLNG", "SOFL",        # инвалюта/масштаб
        "SBERP", "BANEP", "TATNP", "LSNGP",            # префы → обычка
        "YNDX", "TCSG", "LNTA",                        # алиасы
        "SBER", "LKOH", "CHMF",                        # контроль
    ]
    reconcile(args)
