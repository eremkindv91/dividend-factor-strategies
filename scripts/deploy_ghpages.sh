#!/usr/bin/env bash
# Ручной деплой сайта в GitHub Pages (ветка gh-pages) — для обновления цен
# до настройки автоматического cron (update.yml). Запуск из корня репозитория:
#   bash scripts/deploy_ghpages.sh
#
# Делает: пересборку site/data.json (свежие цены MOEX ISS), optional JSON-слои
# и публикацию файлов сайта в orphan-ветку gh-pages. Падает, если критичный
# контракт сайта не собрался или не прошёл валидацию.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="https://github.com/eremkindv91/dividend-factor-strategies.git"
TMP="$(mktemp -d)"

cd "$REPO"
python3 -m src.pipeline.run_all --skip-ocr --allow-network
python3 -m src.pipeline.validate_financials
python3 scripts/build_quality.py
echo "[deploy] пересборка data.json (свежие цены)…"
python3 scripts/build_data.py   # exit!=0 (нет цен/артефакта) прервёт деплой
python3 scripts/build_quality.py   # обновляет market/lot metadata после data.json
python3 market_saw/production/build_marketsaw.py || echo "[marketsaw] пропуск — сохранён предыдущий валидный файл, если он есть"
python3 scripts/build_forward_yield.py || echo "[fwd] пропуск форвардной доходности"
python3 scripts/build_market_history.py
python3 scripts/validate_site_data.py

cp site/index.html site/styles.css site/app.js site/data.json "$TMP/"
[ -f site/methodology.json ] && cp site/methodology.json "$TMP/"
[ -f site/returns.json ] && cp site/returns.json "$TMP/"
[ -f site/marketsaw.json ] && cp site/marketsaw.json "$TMP/"
[ -f site/market_history.json ] && cp site/market_history.json "$TMP/"
[ -f site/marlamov.json ] && cp site/marlamov.json "$TMP/"
[ -f site/quality.json ] && cp site/quality.json "$TMP/"
[ -f site/site_coverage.json ] && cp site/site_coverage.json "$TMP/"
[ -f site/site_financials.json ] && cp site/site_financials.json "$TMP/"
[ -f site/news.json ] && cp site/news.json "$TMP/"
[ -d site/bonds ] && mkdir -p "$TMP/bonds" && cp site/bonds/*.json "$TMP/bonds/"
V="$(git rev-parse --short=8 HEAD)"
python3 - "$TMP/index.html" "$V" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
version = sys.argv[2]
html = path.read_text(encoding="utf-8")
html = html.replace('"app.js"', f'"app.js?v={version}"')
html = html.replace('"styles.css"', f'"styles.css?v={version}"')
path.write_text(html, encoding="utf-8")
PY
touch "$TMP/.nojekyll"
git -C "$TMP" init -q
git -C "$TMP" checkout -q -b gh-pages
git -C "$TMP" add -A
git -C "$TMP" -c user.name="Dmitry Eremkin" -c user.email="eremkindv1991@gmail.com" \
  commit -q -m "deploy: обновление сайта прогноза дивидендов"
echo "[deploy] публикация в gh-pages…"
git -C "$TMP" push -f "$REMOTE" gh-pages
rm -rf "$TMP"
echo "[deploy] готово → https://eremkindv91.github.io/dividend-factor-strategies/"
