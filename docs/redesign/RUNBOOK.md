# RUNBOOK — редизайн dividend-factor-strategies

Как поднять сайт локально, собрать снапшот данных и прогнать регрессионный/скриншот-харнесс.
Спека: [`SPEC.md`](SPEC.md). Запуск — **по одной итерации за сессию** (Приложение A спеки).

## Снапшот данных

- **Дата снапшота: 2026-07-23** (все скриншоты и baseline считаются на нём — иначе before/after бессмысленны).
- 10 CI-генерируемых файлов сняты с Pages в `site/_snapshot/` и скопированы в `site/` только для локали.
  `site/_snapshot/` в `.gitignore`, снапшот-файлы не коммитятся.
- 404 при снятии: **нет** — все 10 файлов отдались (data.json, returns.json, quality.json, marlamov.json,
  marketsaw.json, marketsaw_imoex.json, site_status.json, site_coverage.json, site_financials.json,
  events_calendar.json). Дополнительно сняты секционные файлы (market_history, dividend_calendar, news,
  alfa-index, bonds/*, cbr/*). `build.json` — 404 (ещё не существует, создаётся в Итерации 3, §6.1).

Пересобрать снапшот:
```bash
BASE=https://eremkindv91.github.io/dividend-factor-strategies
mkdir -p site/_snapshot
for f in data.json returns.json quality.json marlamov.json marketsaw.json \
         marketsaw_imoex.json site_status.json site_coverage.json \
         site_financials.json events_calendar.json; do
  curl -sSfL "$BASE/$f" -o "site/_snapshot/$f" && echo "OK $f" || echo "FAIL $f"
done
cp site/_snapshot/*.json site/
```

## Локальный запуск

```bash
python3 -m http.server 8080 --directory site
# http://localhost:8080/  → должен отдавать 200 и рендерить с данными (238 тикеров, 9 разделов)
```

Под подпутём (эмуляция GitHub Pages):
```bash
mkdir -p /tmp/ghp/dividend-factor-strategies && cp -r site/* /tmp/ghp/dividend-factor-strategies/
python3 -m http.server 8081 --directory /tmp/ghp
# http://localhost:8081/dividend-factor-strategies/  → все относительные fetch работают
```
Проверено: root `/` → 200, подпуть → 200, `data.json`/`bonds/screener.json` → 200.

## Tooling (Playwright)

Установлен `playwright` (devDep) + `chromium` headless shell. Системного Chrome нет — Lighthouse
через системный Chrome недоступен (см. «Ограничения»); используется Playwright chromium.

```bash
npm i -D playwright && npx playwright install chromium   # один раз

# Регрессия расчётного слоя (§7): извлекает метрики из PFX_STATE и VIEW через page.evaluate
node tools/regression.js --mode=baseline   # зафиксировать baseline (tests/baseline/*.json)
node tools/regression.js --mode=check       # exit 1 при любом расхождении > 1e-9

# Console-audit по всем 9 разделам (§4.3) — errors/warnings/pageerror/requestfailed
node tools/console-audit.js --tag=console-report-before

# Скриншоты всех разделов во всех вьюпортах (§4.3)
node tools/screenshot.js --tag=before
node tools/screenshot.js --tag=after-iter2 --sections=market,my-portfolio --viewports=1440x1000,390x844
```

Артефакты (`artifacts/`, `node_modules/`) в `.gitignore` — тяжёлые/генерируемые, пересобираются командами выше.

## Состояние Итерации 0 (Bootstrap) — выполнено

| Пункт | Статус |
|---|---|
| Снапшот 10 файлов | ✅ 2026-07-23, 404 нет |
| Локальный запуск root + подпуть | ✅ 200/200, рендерит с данными |
| Playwright + chromium | ✅ установлен, запускается |
| Регрессионный baseline | ✅ 70 детерминированных метрик, exit 0 дважды |
| Console-audit before | ✅ 0 errors / 0 warnings / 0 pageerror / 0 requestfailed (9 разделов) |
| Before-скриншоты | ✅ 63 (7 вьюпортов × 9 разделов) |
| Lighthouse | ❌ НЕ ВЫПОЛНЕНО — нет системного Chrome; axe-core запланирован в Итерации 7 |

## Находки Итерации 0 (для AUDIT/FINANCIAL-CORRECTNESS, Итерация 1)

- **Месячный bootstrap НЕ seed-детерминирован.** `PFX_STATE.boot` (pBeat/pLowerDD/pLoss/cagr/mdd/sharpe)
  меняется между прогонами на той же фикстуре (0.72→0.734…). Дневной `drVarBootstrap` seed-детерминирован
  (MC_SEED=42), но месячный конвейер `pfxCompute` использует несидированный ресемплинг. Вынесено в
  `tests/baseline/portfolio.json → stochastic_not_compared` и НЕ входит в regression-check. Кандидат на
  сидирование в поздней итерации (изменение расчёта — только по согласованию).
- **Короткая история (<60 мес) не представима в текущем снапшоте:** все 232 тикера `returns.json` имеют
  91 мес. Кейс фикстуры задокументирован, но синтетически не воспроизводится до появления IPO-бумаг.
