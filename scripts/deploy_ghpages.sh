#!/usr/bin/env bash
# Ручной деплой сайта в GitHub Pages (ветка gh-pages) — для обновления цен
# до настройки автоматического cron (update.yml). Запуск из корня репозитория:
#   bash scripts/deploy_ghpages.sh
#
# Делает: пересборку site/data.json (свежие цены MOEX ISS) → публикацию только
# файлов сайта в orphan-ветку gh-pages. Падает, если data.json не собрался.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="https://github.com/eremkindv91/dividend-factor-strategies.git"
TMP="$(mktemp -d)"

cd "$REPO"
echo "[deploy] пересборка data.json (свежие цены)…"
python3 scripts/build_data.py   # exit!=0 (нет цен/артефакта) прервёт деплой

cp site/index.html site/styles.css site/app.js site/data.json "$TMP/"
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
