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
LOGO_BUILD="$(mktemp -d)"
LOGO_LAST_GOOD="$(mktemp -d)"

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
python3 scripts/build_dividend_calendar.py
python3 scripts/validate_dividend_calendar.py
python3 scripts/update_events_calendar.py
python3 scripts/validate_events_calendar.py
python3 scripts/check_predeploy_contract.py
python3 scripts/build_site_status.py
python3 scripts/validate_site_data.py

# Логотипы — build-time слой. Без токена или при сбое API ручной deploy сохраняет
# уже опубликованный набор; токен никогда не копируется в каталог Pages.
if ! git clone --quiet --depth 1 --branch gh-pages "$REMOTE" "$LOGO_LAST_GOOD" 2>/dev/null; then
  rm -rf "$LOGO_LAST_GOOD"
  LOGO_LAST_GOOD=""
fi
PREVIOUS=""
if [ -n "$LOGO_LAST_GOOD" ] && [ -f "$LOGO_LAST_GOOD/instrument_logos.js" ]; then
  PREVIOUS="--previous-registry $LOGO_LAST_GOOD/instrument_logos.js"
fi
python3 scripts/build_instrument_logos.py --universe site/data.json --output-dir "$LOGO_BUILD" $PREVIOUS \
  || echo "[instrument-logos] API недоступен — сохраняем last-good"

cp site/index.html site/styles.css site/instrument_logos.js site/instrument_identity.js site/bond_allocator.js site/bond_retail.js site/app.js site/data.json "$TMP/"
[ -d site/assets ] && cp -r site/assets "$TMP/"
if [ -n "$LOGO_LAST_GOOD" ]; then
  [ -f "$LOGO_LAST_GOOD/instrument_logos.js" ] && cp "$LOGO_LAST_GOOD/instrument_logos.js" "$TMP/"
  if [ -d "$LOGO_LAST_GOOD/assets/instruments/companies" ]; then
    mkdir -p "$TMP/assets/instruments"
    cp -R "$LOGO_LAST_GOOD/assets/instruments/companies" "$TMP/assets/instruments/"
  fi
fi
if [ -f "$LOGO_BUILD/instrument_logos.js" ]; then
  cp "$LOGO_BUILD/instrument_logos.js" "$TMP/"
  mkdir -p "$TMP/assets/instruments"
  cp -R "$LOGO_BUILD/assets/instruments/companies" "$TMP/assets/instruments/"
  cp "$LOGO_BUILD/assets/instruments/company_manifest.json" "$TMP/assets/instruments/"
fi
[ -f site/methodology.json ] && cp site/methodology.json "$TMP/"
[ -f site/returns.json ] && cp site/returns.json "$TMP/"
[ -f site/marketsaw.json ] && cp site/marketsaw.json "$TMP/"
[ -f site/market_history.json ] && cp site/market_history.json "$TMP/"
[ -f site/marlamov.json ] && cp site/marlamov.json "$TMP/"
[ -f site/quality.json ] && cp site/quality.json "$TMP/"
[ -f site/site_coverage.json ] && cp site/site_coverage.json "$TMP/"
[ -f site/site_financials.json ] && cp site/site_financials.json "$TMP/"
[ -f site/news.json ] && cp site/news.json "$TMP/"
[ -f site/macro_cbr.json ] && cp site/macro_cbr.json "$TMP/"
# Ниже — файлы, которых ручной скрипт лишился при расхождении с update.yml.
# Расхождение теперь стережёт tests/test_publish_lists.py.
[ -f site/market_pe_current.json ] && cp site/market_pe_current.json "$TMP/"
[ -f site/market_pe_history.json ] && cp site/market_pe_history.json "$TMP/"
[ -f data/market_pe_history_cache.json ] && cp data/market_pe_history_cache.json "$TMP/"
[ -f site/_fallback.json ] && cp site/_fallback.json "$TMP/"
[ -d site/cbr ] && cp -r site/cbr "$TMP/"
[ -f site/alfa-index.json ] && cp site/alfa-index.json "$TMP/"
[ -f site/alfa-index-history.json ] && cp site/alfa-index-history.json "$TMP/"
[ -f site/dividend_calendar.json ] && cp site/dividend_calendar.json "$TMP/"
[ -f site/events_calendar.json ] && cp site/events_calendar.json "$TMP/"
[ -f site/site_status.json ] && cp site/site_status.json "$TMP/"
[ -d site/bonds ] && mkdir -p "$TMP/bonds" && cp site/bonds/*.json "$TMP/bonds/"
[ -d site/ml_strategy ] && mkdir -p "$TMP/ml_strategy" && cp -R site/ml_strategy/. "$TMP/ml_strategy/"
V="$(git rev-parse --short=8 HEAD)"
python3 - "$TMP/index.html" "$V" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
version = sys.argv[2]
html = path.read_text(encoding="utf-8")
html = html.replace('"instrument_logos.js"', f'"instrument_logos.js?v={version}"')
html = html.replace('"instrument_identity.js"', f'"instrument_identity.js?v={version}"')
html = html.replace('"bond_allocator.js"', f'"bond_allocator.js?v={version}"')
html = html.replace('"bond_retail.js"', f'"bond_retail.js?v={version}"')
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
[ -n "$LOGO_LAST_GOOD" ] && rm -rf "$LOGO_LAST_GOOD"
rm -rf "$LOGO_BUILD"
echo "[deploy] готово → https://eremkindv91.github.io/dividend-factor-strategies/"
