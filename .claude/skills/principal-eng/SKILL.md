---
name: principal-eng
description: Инженерная линза (Principal Engineer) для dividend-factor-strategies. Использовать при реализации любой фичи/фикса, работе с app.js, пайплайнами данных, CI/CD, деплоем на gh-pages, и при отладке «на проде не то, что локально». Отвечает: как встроить изменение, ничего не сломав.
---

# Principal Engineer — инженерная линза

Продукт УЖЕ рабочий и живёт в проде. Правило №1: **расширять, не переписывать**.
Минимальные диффы поверх существующего; каждый этап — отдельный коммит, после которого
сайт работоспособен.

## Архитектура (не менять без явного решения)

- Статик GitHub Pages. Vanilla JS: ОДИН `site/app.js` (~6000 строк, `'use strict'`),
  `site/styles.css`, `site/index.html`. Никаких фреймворков/бандлеров/npm.
- Chart.js 4 (CDN) + LightweightCharts. Роутер: `SECTIONS` + hash, секции `data-section`,
  инициализация в `onSectionShown(sec)`.
- Префиксы: портфель `pfx*`, банки `bval*`/`cbr*`, новости `news*`, фаза `saw*`. Новый код —
  в том же стиле и префиксе своего раздела.

## Гочи кодовой базы (каждая стоила часов)

- **TDZ:** все data-глобалы (`DATA, PF_RETURNS, SAW_DATA, SITE_STATUS, EVENTS_DATA…`)
  хойстятся ОДНОЙ строкой `let` в самом верху app.js. Новый глобал — только туда.
- **Рендер завязан на id, не на порядок DOM** — переупорядочивание секций безопасно,
  но id должны быть уникальны на страницу.
- **Кеш браузера:** локальный preview кеширует bare `app.js` → временно `?v=dev*` в
  index.html, ВЕРНУТЬ перед коммитом (на проде CI сам ставит `?v=sha8` через sed).
- **YAML:** двоеточие в `name:` шага без кавычек ломает workflow (GitHub 422 на dispatch).
- **eval в preview:** top-level `let` не виден через `window.X` — читать по имени переменной.
- **macOS bash:** нет `timeout`; фоновые долгие команды — `run_in_background`.

## Данные и CI (критично)

- Многие данные ГЕНЕРЯТСЯ CI и в `.gitignore` (`site/data.json`, `returns.json`,
  `site_financials.json`, `events_calendar.json`…). Прежде чем полагаться «файл закоммичен» —
  `git ls-files <путь>`. Живой сайт может отличаться от локальной копии.
- Пайплайн update.yml: build_data → clean_portfolio_data → events → marketsaw → marlamov →
  financial layer (migrate→unify→build_site_data) → P0-гейт (predeploy contract + last-good
  fallback) → site_status → validate → orphan-push gh-pages → smoke (+rollback).
- Concurrency-группа `gh-pages-publish` сериализует все публикации (update/news/bonds).
  Промежуточные раны могут отменяться — это норма, финальный содержит всё.
- SmartLab-краул (reconcile) НЕ в CI — rate-limit; панель обновляется локально и коммитится.

## Definition of Done (каждая фича)

1. `node --check site/app.js` + `python3 -m py_compile` изменённых скриптов.
2. Preview-проверка по ux-terminal (desktop + mobile 375 + console 0 errors).
3. Коммит с содержательным сообщением (что/почему/как проверено), push в `dividend-site`.
4. **Деплой сам:** `gh workflow run update.yml --ref dividend-site` (cron только будни —
   иначе фича висит незадеплоенной), `gh run watch` в фоне.
5. **Live-проверка:** `app.js?v=<sha8>` на проде + grep ключевой функции в опубликованном
   app.js + при данных — `scripts/smoke_public_site.py`. Фича не «готова», пока не
   подтверждена на live.
6. Существующие вкладки не сломаны (news/market/portfolio/stocks/strategies/bonds/cbr/pro
   открываются, консоль чистая).

## Правки данных-пайплайнов

Правило хирургии: понять точный механизм бага (воспроизвести числа), править минимально,
прогнать на реальных данных, сравнить до/после по счётчикам (сколько тикеров затронуто),
и только потом коммитить. Урок: «исправление» сплитов чуть не занулило 24 здоровых тикера.
