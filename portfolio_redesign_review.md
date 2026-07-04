# Portfolio X-Ray & Rebalance Lab — самокритика и аудит данных (перед реализацией)

Дата: 2026-07-04. Автор ревью: Claude Code. Цель — заземлить редизайн вкладки «Портфель»
на **фактически доступные данные проекта**, а не на идеальную спеку, и честно отделить то, что
считается корректно, от того, что дало бы ложную точность.

## 0. Ключевая находка о данных (определяет всё)

| Данные | Что есть | Частота | Следствие |
|---|---|---|---|
| `returns.json.data[тикер]` | **ценовой** месячный ретёрн, 232 тикера, 90 мес (2019-01…2026-06) | **месячная** | Дневной VaR/rolling 63-252д/EWMA-daily/дневной backtest — **невозможны** |
| `returns.json.div[тикер]` | месячная дивдоходность (реальные выплаты MOEX) | месячная | total return = data + div (честно) |
| `marketsaw.series` | MCFTR уровень, дневной 2003→2026 (5843 т.) | дневная | ресемплю в месячные → бенчмарк под 90 мес |
| `data.json.tickers[]` | price, sector, mcap, adv, dividend_forecast(DPS), dividend_yield_expected, cut_risk(0..1), payout, quality_barra, mom_score, stability_score, vol_ann, nd_ebitda | статик (asof 2026-06-26) | дивстресс/аллокация/диагностика — сильны |
| RFR | текущее значение (`marlamov.meta.rfr` / `data.meta.rf_ofz`) | **точка, не история** | excess-метрики (Sharpe/alpha/Treynor) — с явной оговоркой «RFR-константа» |
| per-ticker beta | нет в data.json | — | считаю CAPM-регрессией на месячных |

**Вывод:** проект даёт честный **месячный total-return слой** (цена+дивиденд) на ~90 наблюдений и
дневной MCFTR. Это достаточно для профессионального **месячного** risk/performance-терминала, но
НЕ для дневного VaR-движка из спеки. Дневные части → честно `unavailable (нет дневных данных по бумагам)`.

## 1. Что сейчас слабо для проф-аналитика
Текущий модуль (`renderMyPortfolio`): парсинг `ticker;qty;avg`, стоимость, P&L, дивдоходность,
базовые риск-метрики корзины из returns.json, простые action-флаги. Нет сравнения с бенчмарком,
нет alpha/beta, нет VaR/CVaR, нет вклада в риск, нет дивстресс-сценариев, нет ребаланса, нет memo,
нет слоя качества данных. Выглядит как калькулятор, не как терминал.

## 2. Отсутствующие проф-метрики
Alpha/beta/R²/TE/IR/Treynor/capture; VaR/CVaR (hist+parametric+Cornish-Fisher); component/marginal
VaR; risk budget; дивстресс (base/conservative/stress/crisis) + yield-trap; bootstrap устойчивости;
сценарный ребаланс; equity/drawdown/rolling графики; investment memo; data-quality confidence.

## 3. Что реально доступно (считаю честно)
Total return портфеля (data+div, месячно), CAGR, ann.vol (×√12), MaxDD (месячный), Sharpe/Sortino
(RFR-константа, оговорка), Calmar; CAPM alpha/beta/R²/corr/TE/IR/Treynor/up-down-capture (≈90 мес);
**месячные** hist VaR/CVaR 95/99, parametric Gaussian VaR, Cornish-Fisher (skew/kurt); ковариация с
Ledoit-Wolf-подобным shrinkage → component/marginal VaR и risk budget; rolling 12/24/36-**мес** VaR;
bootstrap (resample месяцев, горизонт 12 мес); дивстресс из data.json (dividend_forecast × payout_prob
= 1−cut_risk); аллокация по сектор/cut_risk/beta/yield/liquidity(adv); position diagnostics; memo;
data-quality по длине истории и покрытию.

## 4. Чего не хватает (честно unavailable / low confidence)
- **Дневных** данных по бумагам → дневной VaR, rolling 63/126/252-дн, EWMA λ=0.94 daily, дневной
  VaR-backtest, intraday. Показываю `unavailable`, заменяю месячными аналогами с явной подписью.
- **Истории RFR** → excess-метрики на RFR-константе (оговорка), либо raw.
- Аналитических target price — не используем (нет легального стабильного источника). DCF-модуль — future.
- Надёжной ликвидностной классификации кроме `adv` (среднедневной оборот) — грубые бакеты.
- State/private ownership mapping — только если есть аккуратный источник; иначе не строим.

## 5. Метрики, которые можно считать честно
Все из п.3 — на месячной базе, с confidence по длине ряда конкретной бумаги (у IPO-шек < 90 мес).

## 6. Метрики с риском ложной точности (осторожно/оговорка)
- Хвостовой VaR/CVaR 99% на ~90 месяцах — широкий доверительный интервал → `low confidence` при <36 мес.
- Cornish-Fisher при экстремальном kurtosis может «взрываться» → клампить/фоллбек на historical.
- Component VaR на 232×90 ковариации — сингулярна без shrinkage → **обязателен shrinkage**, иначе fallback
  `approx = weight_i × individual_vol_i` с подписью «approximated due to insufficient covariance history».
- Alpha t-stat на 90 мес — считаю, но помечаю, что это историческая оценка, не прогноз.
- Bootstrap-перцентили — resampling истории, НЕ прогноз; подпись обязательна.

## 7. Price vs total return
`returns.json.data` = **ценовой**, `div` = дивиденд. Total = data+div. Везде считаю и подписываю
total return; ценовой P&L (текущая−средняя) держу отдельно и не смешиваю с total-return метриками.
MCFTR — индекс **полной** доходности → сопоставим с портфельным total return (корректно).

## 8. Где можно случайно выдать бэктест за факт
Портфель строится из ТЕКУЩИХ позиций и их исторических ретёрнов = **backfilled portfolio по текущему
составу**, а НЕ фактическая история сделок пользователя. Везде явная плашка. Веса — либо по рыночной
стоимости на каждый месяц (drifting), либо фиксированные текущие; выбор подписываю.

## 9. Как не превратить в визуальный шум
Модули в свёрнутых `<details>` (как остальные вкладки); верхний Portfolio X-Ray = один экран
диагноза; KPI-карточки компактные; цвет только семантический (красный=риск, зелёный=улучшение,
жёлтый=caution, серый=unavailable); графики Chart.js читаемые; тяжёлое (bootstrap/rolling) — по клику.

## 10. Что сделает вкладку institutional
Rule-based диагноз одной строкой; сравнение с MCFTR (equity/drawdown); alpha/beta с интерпретацией;
VaR в ₽ и %; вклад бумаг в риск и в дивпоток; дивстресс-сценарии; investment-committee memo тоном
аналитика; честный data-quality слой с confidence на каждой метрике; никаких «купи/продай» и фейков.

## План реализации (фазы, монотонно вперёд)
1. **Данные+парсер**: расширенный `parsePortfolioInput` (запятая-десятичный, #-комменты, CSV, дубли),
   валидация, enrich из data.json/returns.json; ресемпл MCFTR→месячный; RFR-константа.
2. **Ядро метрик** (чистые функции): perf, CAPM, VaR-движок (месячный), ковариация+shrinkage,
   component VaR, risk budget, дивстресс, bootstrap, heuristic rebalancer, allocation, diagnostics,
   data-quality, memo.
3. **UI**: Portfolio X-Ray → vs MCFTR → Alpha/Beta → Risk/VaR → Risk Budget → Дивстресс → Bootstrap →
   Rebalancer → Allocation → Position table → Memo → Methodology. Chart.js, responsive, tooltips.
4. **Проверки**: node -c, браузер (нет ошибок консоли), мобилка, остальные вкладки целы.

Приоритет честности над полнотой: дневные фичи спеки отдаю как `unavailable` с указанием причины,
а не имитирую дневной ряд из месячного.
