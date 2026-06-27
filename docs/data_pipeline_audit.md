# Data Pipeline Audit

Дата аудита: 2026-06-27

## Краткий вывод

Текущий сайт уже имеет рабочий статический pipeline для дивидендной модели, цен MOEX, Market Saw, forward yield и облигаций. Фундаментальные данные SmartLab уже есть в репозитории и используются как часть исторической панели/артефакта модели, но отдельного production-like unified layer с provenance, conflict flags и quality score пока нет.

Первый безопасный инкремент должен не менять текущие исходные файлы и frontend-контракт, а создать параллельный слой:

- `data/processed/smartlab_fundamentals.parquet`
- `data/unified/financial_facts_unified.parquet`
- `data/unified/company_financials_unified.parquet`
- `data/unified/site_financials.json`
- `data/unified/site_coverage.json`

## Текущие источники данных

### SmartLab / фундаментальные данные

- `results/smartlab/live_forecast.csv` — текущая выгрузка прогнозных/дивидендных полей по тикерам.
- `results/smartlab/reconcile.csv` — отчёт сверки SmartLab с панелью; сейчас файл уже грязный в рабочем дереве и не должен включаться в новые коммиты без отдельной команды.
- `data/panels_final/panel_russia_final_smartlab.csv` — gitignored итоговая панель с дозаполнением SmartLab.
- `data/panels_final/panel_russia_final.csv` — базовая историческая панель.
- `data/company_sources.csv` — управляемый реестр официальных страниц/ручных ссылок на отчёты. Пустые или отсутствующие URL не заменяются синтетикой.
- `data/report_index.parquet` — generated metadata-only индекс найденных отчётов; не хранит PDF и не публикуется как источник истины без последующего parse/validation.
- `scripts/smartlab_fetch.py` — точечный HTML fetch SmartLab `/q/{TICKER}/f/y/` с вежливой задержкой и alias-логикой.
- `scripts/smartlab_reconcile.py` — сверка и безопасное дозаполнение панели; исходный `panel_russia_final.csv` не перезаписывается.

### Сайт

Текущий frontend находится в:

- `site/index.html`
- `site/app.js`
- `site/styles.css`

Текущие site JSON:

- `site/data.json` — основной контракт акций: `meta`, `tickers`.
- `site/returns.json` — месячные доходности для конструктора портфеля.
- `site/marketsaw.json` — текущая фаза рынка по MCFTR.
- `site/marlamov.json` — forward dividend yield / Yield2.
- `site/methodology.json` — методология по разделам.
- `site/bonds/*.json` — облигационный скринер, кривая и портфели.

Важные особенности:

- `site/data.json`, `site/returns.json`, `site/marketsaw.json`, `site/marlamov.json` gitignored и генерируются pipeline/workflow.
- Frontend читает старые site JSON напрямую и дополнительно умеет читать `site_coverage.json` как слой прозрачности источников. Старый рабочий контракт `data.json` не заменён.

### Модель и артефакты

- `model_output/forecast_rf.json` — замороженный артефакт прогноза дивидендной модели.
- `model_output/returns.json` — источник для `site/returns.json`.
- `model_output/momentum.json` — momentum-добавка.

### Workflows

- `.github/workflows/update.yml` — ежедневный build/deploy `site/` в `gh-pages`; уже содержит JSON validation step.
- `.github/workflows/update.yml` также собирает SmartLab-only unified financial layer, валидирует его и публикует `site_coverage.json` / `site_financials.json` как additive JSON.
- `.github/workflows/bonds_update.yml` — отдельный тяжёлый workflow облигаций с additive publish.
- `.github/workflows/update_financial_data.yml` — отдельный CI/scheduled smoke workflow для audit, SmartLab migration, unified layer, tests и upload artifacts без второго деплоя.
- `.github/workflows/train.yml`, `refresh.yml`, `momentum.yml` — дополнительные research/update workflows.

## Текущие тесты

- `market_saw/tests/test_market_saw.py`
- `scripts/validate_site_data.py` как контрактный валидатор JSON сайта.
- `tests/test_*.py` для нового финансового pipeline: audit, SmartLab migration, company registry, document classifier, IFRS mapping, numeric normalization, financial validators, source resolver, deduplication, unified layer и smoke `run_all`.

## Текущие поля SmartLab-панели

Основные финансовые поля в `data/panels_final/panel_russia_final_smartlab.csv`:

- `ticker`
- `year`
- `sector`
- `revenue_mln`
- `ebitda_mln`
- `net_profit_mln`
- `equity_mln`
- `assets_mln`
- `net_debt_mln`
- `total_debt_mln`
- `cash_mln`
- `CFO_`
- `CAPEX_`
- `FCF`
- `dps_rub`
- `payout_ratio_pct`

Эти поля можно безопасно мигрировать в facts-формат с `source_name=smartlab`, `source_type=aggregator`, `source_priority=7`, `is_legacy_data=true`, `quality_score=70`.

## Риски поломки

- Перезапись `results/smartlab/reconcile.csv` или `data/panels_final/panel_russia_final_smartlab.csv` может затереть уже скачанные/сверенные данные.
- `site/app.js` остаётся крупным монолитом; frontend-изменения нужно делать маленькими шагами.
- `update.yml` публикует только заранее перечисленные файлы; новые `site_coverage.json`/`site_financials.json` не попадут в Pages, пока workflow не будет расширен.
- OCR/LLM нельзя включать до появления provenance, confidence и manual_review-gates.
- SmartLab и IFRS могут различаться по единицам, валюте, стандарту отчётности и трактовке долга/EBITDA.
- Stage 2 report discovery сейчас metadata-only: если в `data/company_sources.csv` нет реальных URL, результат честно остаётся пустым.

## Что будет добавлено первым инкрементом

- Аудит-команда: `python -m src.pipeline.audit_existing_data`
- Миграция SmartLab в processed layer: `python -m src.pipeline.migrate_existing_smartlab_data`
- Базовая дедупликация и source resolver.
- Manual override layer: `data/manual_overrides/financial_facts.csv` при наличии перебивает SmartLab/IFRS и фиксируется как `source_name=manual_override`.
- Metadata-only discovery: `data/company_sources.csv` → `data/report_index.parquet` без скачивания PDF/OCR. Это готовит official IFRS слой, но не подменяет SmartLab baseline.
- Unified layer поверх уже имеющегося SmartLab baseline.
- JSON для будущего сайта: `site_financials.json` и `site_coverage.json`.
- Frontend-блок «Покрытие и качество данных» во вкладке «Методология», без замены старых расчётов.
- `run_all --smartlab-only`, который не ходит во внешние источники и не требует OCR/API keys.
- Тесты на migration, deduplication, source resolver, numeric normalization и smoke pipeline.

## Файлы, которые будут созданы

- `src/pipeline/*.py`
- `src/unification/*.py`
- `src/normalization/*.py`
- `src/quality/*.py`
- `src/extraction/*.py`
- `src/ocr/*.py`
- `src/data_sources/*.py`
- `data/mapping/ifrs_line_items.yaml`
- `tests/test_*.py`
- Generated outputs under `data/processed/`, `data/unified/`, `data/manual_review/`, `data/backups/`

## Файлы, которые могут быть изменены

- `.github/workflows/update_financial_data.yml` — safe CI workflow для SmartLab-only baseline, тестов и upload artifacts без второго деплоя сайта.
- `.github/workflows/update.yml` — daily deploy теперь собирает и публикует additive unified financial JSON.
- `site/index.html`, `site/app.js`, `site/styles.css` — добавлен блок покрытия/качества данных во вкладке «Методология».
- `.gitignore` — новые generated parquet/json/backups/manual_review/logs исключены из коммита.

В этом первом инкременте текущий frontend и daily site workflow не должны ломаться.
