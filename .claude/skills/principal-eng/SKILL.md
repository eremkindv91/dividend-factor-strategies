---
name: principal-eng
description: "Инженерия dividend-factor-strategies — архитектура, гочи кодовой базы, карта данных/пайплайнов, тесты, безопасность, производительность, миграции схем. Использовать при реализации любого изменения кода/пайплайнов/CI и при отладке «на проде не то, что локально». Выкат — через skill ship."
---

# Principal Engineer — как встроить, ничего не сломав

## Purpose / Scope

Продукт живёт в проде; правило №1 — **расширять, не переписывать**: минимальные диффы,
каждый коммит оставляет сайт рабочим. Scope: архитектура, код, пайплайны, тесты,
security, perf, миграции. НЕ занимается: «стоит ли строить» (pm-gate), выкатом (ship).

## When to invoke / When not

- Invoke: реализация после вердикта; любые правки app.js/скриптов/workflows; отладка
  расхождений local↔prod.
- Not: чистые продуктовые обсуждения; правка одних текстов UI (достаточно ux-terminal+ship).

## Архитектура (менять только решением L4)

Статик GitHub Pages · vanilla JS: один `site/app.js` (~6500 строк, 'use strict'),
`styles.css`, `index.html` · без фреймворков/бандлеров/npm · Chart.js 4 + LightweightCharts
(CDN; новые CDN-зависимости — решение L4) · роутер `SECTIONS`+hash, init в
`onSectionShown` · префиксы разделов: `pfx*` портфель, `bval*/cbr*` банки, `news*`,
`saw*` — новый код в префиксе своего раздела.

## Гочи (каждая стоила часов — проверять при касании)

- **TDZ:** все data-глобалы — одной `let`-строкой в самом верху app.js; новый — туда же.
- **Кеш:** локальный preview кеширует bare `app.js` → на время проверки `?v=dev*` в
  index.html и ВЕРНУТЬ до коммита (прод получает `?v=sha8` через sed в CI).
- Рендер по id, не по порядку DOM; id уникальны.
- YAML: `name:` с двоеточием без кавычек → workflow 422.
- preview eval: top-level `let` не виден как `window.X`.
- macOS: нет `timeout`; долгие команды — run_in_background.

## Карта данных (что откуда; tracked? — проверять `git ls-files`)

| Артефакт | Генератор | В git? |
|---|---|---|
| site/data.json, returns.json, marketsaw.json, marlamov.json, events_calendar.json, site_status.json | CI update.yml (build_data → clean_portfolio_data → events → marketsaw → marlamov) | нет (gitignore) |
| site/site_financials.json | migrate→unify→build_site_data (CI) | нет |
| data/panels_final/panel_russia_final_smartlab.csv | scripts/smartlab_reconcile.py ALL (ЛОКАЛЬНО, не CI — rate-limit) | ДА |
| site/cbr/*, site/bonds/* | update-cbr-banks.yml / bonds_update.yml | нет |
| site/news.json | news.yml (3 crona) | да (site/news.json) |

Вывод: живой сайт ≠ локальная копия; прод проверяется только на проде (ship).

## Тесты (в репо ~35 pytest-файлов — использовать!)

Правка пайплайна/скрипта → прогнать целевые тесты: `python3 -m pytest tests/test_<область>* -q`
(есть test_market_history, test_news_pipeline, test_smartlab_*, test_build_site_data,
test_financial_validators…). Новый JSON-контракт → валидатор + тест в том же коммите.
Минимум для любого кода: `node --check site/app.js`, `python3 -m py_compile` изменённых.

## Security / Privacy

Секреты — только GitHub Secrets через `os.getenv` (в коде/коммитах ключей нет — проверять
diff) · любые внешние строки в DOM — через `esc()` (XSS) · портфель пользователя не
покидает браузер; добавление любой отправки данных наружу — блокируется до явного
решения владельца · сторонние домены — только уже используемые (MOEX ISS, cbr.ru, CDN).

## Performance budget

Вкладка не грузит >500KB JSON без lazy-подхода (прецедент: bank_timeseries 423KB —
на границе) · тяжёлые данные — по требованию раздела (onSectionShown), не на старте ·
без новых блокирующих скриптов в `<head>`.

## Миграции схем и состояния

JSON-контракты — additive-only (новые поля, не переименования); ломающее изменение →
версия в meta + одновременная правка всех читателей + валидатора · localStorage-ключи
версионируются (`….v1` → `….v2` с миграцией чтения) · «feature flag» на статике =
блок скрыт (`hidden`) до готовности — вместо мёртвых CTA.

## Хирургия данных (обязательный протокол L2/L3 фиксов)

1) Воспроизвести точный механизм числа до правки; 2) править минимально;
3) прогон на реальных данных; 4) счётчики до/после (сколько тикеров/строк затронуто) —
неожиданный охват = стоп; 5) целевые pytest; 6) коммит с числами в сообщении.

## Blocking / Warnings

Blocking: переписывание работающего модуля, когда возможно расширение · коммит с
упавшим `node --check`/py_compile/целевыми тестами · новый глобал вне TDZ-строки ·
секрет в диффе. Warning: дифф >300 строк на «маленькую» фичу — разбить на этапы.

## Output contract / DoD / Interaction

Реализация = код + тесты + пройденные проверки этого файла; выкат и live-верификация —
строго через **ship** (не дублировать здесь). Пример: «добавь колонку в таблицу банков» →
расширить `bvalTable` cols (не новая таблица), colspan detail-строк обновить, `col-sec`
для мобайла, тултип, затем ship.
