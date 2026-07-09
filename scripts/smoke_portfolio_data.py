#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Публичная проверка портфельных data-level фиксов на ЖИВОМ сайте (после deploy).

Проверяет на gh-pages:
  data.json     — T дивдоходность вправлена (< 15%, а не ~37% до-сплитная);
                  SNGS и SNGSP присутствуют со status=no_model_coverage и реальной ценой;
  returns.json  — meta.series_status помечает split-like ряды needs_adjustment (TRNFP и др.).

exit 1 → фиксы не доехали на публичный сайт. Устойчив к лагу Pages CDN (ретраи).
CLI: python scripts/smoke_portfolio_data.py [--base URL] [--retries N]
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

UA = {"User-Agent": "dividend-site/smoke-portfolio", "Cache-Control": "no-cache"}


def base_url() -> str:
    if "--base" in sys.argv:
        return sys.argv[sys.argv.index("--base") + 1].rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        o, n = repo.split("/", 1)
        return f"https://{o}.github.io/{n}"
    return "https://eremkindv91.github.io/dividend-factor-strategies"


def fetch(url: str, retries: int):
    last = None
    for a in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url + f"?smoke={int(time.time())}", headers=UA), timeout=25) as r:
                return json.loads(r.read().decode("utf-8")), None
        except Exception as e:  # noqa: BLE001
            last = e; time.sleep(min(30, 5 * (a + 1)))
    return None, str(last)[:80]


def main() -> int:
    base = base_url()
    retries = int(sys.argv[sys.argv.index("--retries") + 1]) if "--retries" in sys.argv else 8
    sys.stdout.write(f"[smoke-pf] {base}\n")
    fails = []

    data, e = fetch(f"{base}/data.json", retries)
    if e:
        fails.append(f"data.json недоступен ({e})")
    else:
        tk = {t["ticker"]: t for t in data.get("tickers", [])}
        t = tk.get("T") or {}
        yld = t.get("dividend_yield_expected")
        if isinstance(yld, (int, float)) and yld > 15:
            fails.append(f"T дивдоходность {yld}% (>15% — до-сплитный мусор не вправлен)")
        else:
            sys.stdout.write(f"  [OK] T дивдоходность = {yld} ({t.get('dividend_status')})\n")
        for s in ("SNGS", "SNGSP"):
            x = tk.get(s)
            if not x:
                fails.append(f"{s} отсутствует в data.json")
            elif x.get("status") != "no_model_coverage":
                fails.append(f"{s} status={x.get('status')} (ожидался no_model_coverage)")
            else:
                sys.stdout.write(f"  [OK] {s} присутствует (цена {x.get('price')}, no_model_coverage)\n")

    ret, e = fetch(f"{base}/returns.json", retries)
    if e:
        fails.append(f"returns.json недоступен ({e})")
    else:
        ss = (ret.get("meta") or {}).get("series_status") or {}
        if ss.get("TRNFP") != "needs_adjustment":
            fails.append(f"returns.json series_status[TRNFP]={ss.get('TRNFP')} (ожидался needs_adjustment)")
        else:
            sys.stdout.write(f"  [OK] series_status: {len(ss)} рядов needs_adjustment (TRNFP включён)\n")

    if fails:
        sys.stderr.write("\n[smoke-pf] data-level фиксы НЕ на публичном сайте:\n  - " + "\n  - ".join(fails) + "\n")
        return 1
    sys.stdout.write("[smoke-pf] все портфельные data-level фиксы на публичном сайте — OK\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
