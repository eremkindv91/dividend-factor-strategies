#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0: public smoke-test. После публикации проверяет, что ЖИВОЙ сайт (gh-pages CDN)
реально отдаёт непустые, парсящиеся данные — а не 404/битьё/пустышку.

Проверяет:
  index.html      — 200 + содержит app.js и вкладку «Портфель» (data-section="my-portfolio");
  data.json       — 200 + JSON + tickers непустой + есть price_asof;
  marketsaw.json  — 200 + JSON + index == MCFTR;
  site_status.json— 200 + JSON + overall in {fresh,stale,fallback}.

Устойчив к лагу Pages CDN: ретраи с бэкоффом. exit 1 → деплой считать неуспешным
(алерт/ролбэк-решение принимает workflow). Чистый stdlib.

CLI:
  python scripts/smoke_public_site.py                       # base из GITHUB_REPOSITORY
  python scripts/smoke_public_site.py --base https://... --retries 8
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

UA = {"User-Agent": "dividend-site/smoke", "Cache-Control": "no-cache"}


def base_url() -> str:
    if "--base" in sys.argv:
        return sys.argv[sys.argv.index("--base") + 1].rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}"
    return "https://eremkindv91.github.io/dividend-factor-strategies"


def fetch(url: str, retries: int, as_json: bool):
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(url + ("?smoke=" + str(int(time.time()))), headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                body = r.read().decode("utf-8", "replace")
            return (json.loads(body) if as_json else body), None
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(30, 5 * (a + 1)))
    return None, str(last)[:80]


def main() -> int:
    base = base_url()
    retries = int(sys.argv[sys.argv.index("--retries") + 1]) if "--retries" in sys.argv else 8
    sys.stdout.write(f"[smoke] {base} (retries={retries})\n")
    fails = []

    html, e = fetch(f"{base}/index.html", retries, False)
    if e:
        fails.append(f"index.html недоступен ({e})")
    elif "app.js" not in html or 'data-section="my-portfolio"' not in html:
        fails.append("index.html без app.js/вкладки Портфель")
    else:
        sys.stdout.write("  [OK] index.html\n")

    data, e = fetch(f"{base}/data.json", retries, True)
    if e:
        fails.append(f"data.json недоступен ({e})")
    elif not (isinstance(data, dict) and data.get("tickers") and (data.get("meta") or {}).get("price_asof")):
        fails.append("data.json пуст/без price_asof")
    else:
        sys.stdout.write(f"  [OK] data.json ({len(data['tickers'])} тикеров, price_asof={data['meta']['price_asof']})\n")

    saw, e = fetch(f"{base}/marketsaw.json", retries, True)
    if e:
        fails.append(f"marketsaw.json недоступен ({e})")
    elif (saw.get("index") or "").upper() != "MCFTR":
        fails.append(f"marketsaw.index={saw.get('index')!r} (ожидался MCFTR)")
    else:
        sys.stdout.write("  [OK] marketsaw.json (MCFTR)\n")

    # site_status + news — не критично для ролбэка (новый файл/лаг CDN не должен откатывать сайт)
    if "--core-only" not in sys.argv:
        st, e = fetch(f"{base}/site_status.json", retries, True)
        if e:
            fails.append(f"site_status.json недоступен ({e})")
        elif st.get("overall") not in ("fresh", "stale", "fallback"):
            fails.append(f"site_status.overall={st.get('overall')!r} (broken?)")
        else:
            sys.stdout.write(f"  [OK] site_status.json (overall={st['overall']})\n")
        news, e = fetch(f"{base}/news.json", retries, True)
        if e:
            fails.append(f"news.json недоступен ({e})")
        elif not (news.get("generated_at") and isinstance(news.get("market_snapshot"), list)):
            fails.append("news.json без generated_at/market_snapshot (broken)")
        else:
            sys.stdout.write(f"  [OK] news.json (generated_at={str(news.get('generated_at'))[:16]})\n")

    if fails:
        sys.stderr.write("\n[smoke] ПУБЛИЧНЫЙ САЙТ НЕ ПРОШЁЛ ПРОВЕРКУ:\n  - " + "\n  - ".join(fails) + "\n")
        return 1
    sys.stdout.write("[smoke] публичный сайт отдаёт валидные данные — OK\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
