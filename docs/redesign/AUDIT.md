# AUDIT — находки Итерации 1 (код не менялся)

Факты собраны на ветке `redesign/audit` (от `redesign/bootstrap`), снапшот данных 2026-07-23.
Финансовые находки вынесены в [`FINANCIAL-CORRECTNESS.md`](FINANCIAL-CORRECTNESS.md), карта
неприкасаемого — в [`DOM-CONTRACT.md`](DOM-CONTRACT.md).

## Профиль (подтверждено grep)

| Метрика | Значение |
|---|---|
| `app.js` | 8188 строк; `styles.css` 2851; `index.html` 489 |
| `innerHTML` / `addEventListener` | 132 / 79 |
| `getElementById` / `querySelector` | 221 / 55 |
| inline `onclick`-функции в разметке | **2** (`setMarketSawIndex`, `openDividendCalendarTab`) |
| `fetch(` / `cache:'no-store'` / `Date.now()` | 27 / 27 / **37** |
| `target="_blank"` / из них `rel="noopener…"` | 12 / **12** (все закрыты ✓) |
| CSP / `defer` на app.js | **0 / 0** (отсутствуют) |
| localStorage-ключей | 2 (`myPortfolio.v1`, `dividendCalendarFilters.v2`) |
| `@media` / из них на `760px` | 42 / **29** |
| разделов (`data-section`) | 9 |

## Находки (сила: 🔴 высокая / 🟠 средняя / 🟡 низкая)

1. 🔴 **Кэш браузера отключён полностью** — `cache:'no-store'`×27 + `Date.now()`×37. При ~2 МБ статики
   каждый визит качает всё заново. Главный измеримый выигрыш редизайна по скорости (§6.1). Файл:
   везде в `load*`-обёртках `app.js`.
2. 🔴 **Ложная точность финансовых метрик** — 91 мес истории, Sharpe/Sortino/beta по 2 знака без
   интервала/n (`app.js:1550-1551, 2047-2050`). См. FINANCIAL-CORRECTNESS §5.2.
3. 🔴 **НДФЛ захардкожен 13%** (`0.87`, `app.js:835, 7312`) — не учитывает шкалу 13/15% с 2025, ЛДВ, ИИС.
4. 🟠 **Bootstrap месячный не seed-детерминирован** (находка Итерации 0) — `PFX_STATE.boot` плавает
   между прогонами; дневной `drVarBootstrap` сидирован (MC_SEED=42), месячный `pfxCompute` — нет.
5. 🟠 **Облигационные ловушки не размечены** — нет полей оферта/флоатер/амортизация/НКД/bid-ask/G-спред
   в `bonds/screener.json` (FINANCIAL-CORRECTNESS §5.6).
6. 🟠 **Брейкпоинты не систематизированы** — 42 медиазапроса, 29 на 760px, разброс значений (§16).
   Высокорисковая консолидация → Итерация 7, не раньше.
7. 🟠 **`app.js` без `defer`; тяжёлые разделы** (cbr 484 КБ, market_history 480 КБ) — проверить lazy.
8. 🟠 **Нет CSP**; `aria-live` на контейнерах данных (перечитывание таблиц скринридером, §6.3).
9. 🟡 **`section-intro` почти в каждой секции** — длинные вводные тексты на постоянном верху (§10).
10. 🟡 **a11y-дефекты:** `.pro-card` = `<article role="button" tabindex="0">` (index.html:418–433);
    `.strategy-mode` role="tab" без `aria-selected`/`aria-controls`; `✦ Тарифы` — символ в нав (§9).

## Что сделано правильно (не переписывать — §0.2)

`drVarCF` (gate insufficient_history/unstable_kurtosis/numerical_instability, без тихого клампа);
`drVarBootstrap` (seeded, авто block/iid по автокорреляции квадратов); `drVarBacktest`+Kupiec
(3.841); `drBeta/drCov/drStd` (DDOF=1); `drRiskContribution` (MRC/CRC/PCR, ×√252); `esc()`+`*SafeUrl()`;
бенчмарк MCFTR (total return). Задача редизайна — упаковка, не переписывание.

## Проверки Итерации 1

Код сайта не менялся → `node tools/regression.js --mode=check` = 0 расхождений (baseline Итерации 0);
console-audit before = 0 errors. Итерация — только документы.
