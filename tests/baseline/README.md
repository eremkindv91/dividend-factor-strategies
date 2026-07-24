# Регрессионный baseline расчётного слоя (§7)

Зафиксирован в Итерации 0 на снапшоте данных **2026-07-23** и фикстуре
[`tests/fixtures/portfolio.json`](../fixtures/portfolio.json). **Расхождение = откат изменения, а не
обновление baseline** (§7.3).

```bash
node tools/regression.js --mode=check    # exit 1 при любом расхождении > 1e-9
```

## Зафиксировано

| Файл | Что | Как извлекается |
|---|---|---|
| `portfolio.json` | 70 детерминированных метрик портфеля (value/pnl/cagr/vol/beta/alpha/r2/te/ir/treynor/capture/Sharpe/Sortino/Calmar/VaR hist·normal·CF 95·99/CVaR/skew·kurt/HHI·effN·top3·top5/дивпоток gross·prob·at-risk/backtest Kupiec/component-VaR по позициям) + исключённые позиции с причиной | `page.evaluate` из глобала `PFX_STATE` (месячный конвейер `pfxCompute`) |
| `screener.json` | топ-20 скринера акций при дефолтных фильтрах (тикер, verdict_score, ожидаемая дивдоходность) | `page.evaluate` из глобала `VIEW` |

Фикстура (9 позиций) покрывает: полную историю, преф+обычку (SBER/SBERP), алиас (TCSG→T),
доминирующую позицию ≥25% (SBER ~56% → алерт концентрации), бумагу без дивистории (APTK),
`DR_EXCL no_data` (SNGS — есть в data.json, нет в returns.json → исключена из риска).

## НЕ сравнивается (стохастика — находка §7.3)

`portfolio.json → stochastic_not_compared`: месячный bootstrap (`PFX_STATE.boot`:
pBeat/pLowerDD/pLoss/cagr/mdd/sharpe) **не seed-детерминирован** — меняется между прогонами. Дневной
`drVarBootstrap` сидирован (MC_SEED=42), месячный `pfxCompute` — нет. Зафиксировано для справки,
из regression-check исключено. Сидирование месячного bootstrap — кандидат на согласованное изменение
расчёта в поздней итерации.

## Отложено (baseline к добавлению в след. итерациях, §7.2)

Стратегии (quality/momentum/marlamov/optmv — состав и веса), shortlist облигаций (SECID+YTM+YTM-net+
G-спред), KPI «Банки РФ» по 3 банкам. Требуют триггера конструктора/ленивой загрузки соответствующих
разделов; добавляются перед итерациями, которые их затрагивают (6). Портфель и скринер — самый
нагруженный и рискованный расчётный слой — зафиксированы первыми.
