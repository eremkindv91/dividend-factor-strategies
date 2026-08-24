# Bond Analytics Engine v4

## Назначение

V4 отделяет возможность корректно анализировать выпуск от возможности включить его в портфель. Основной маршрут:

`MOEX ISS -> structure classification -> cash flows -> valuation engine -> relative value -> eligibility -> public artifacts`.

Газпром К2 не имеет отдельной production-ветки. Его значения находятся только в golden fixture `tests/fixtures/bonds/k2_golden.json` и проверяют общий perpetual/resettable engine.

## Режимы

- **Надёжный портфель** использует неизменённый allocator и contracts v3. Сложные структуры выключены по умолчанию.
- **Все возможности** использует отдельный config-driven score и integer-lot allocator. Ограничения по рейтингу, ликвидности, эмитенту, сектору и структурам не ослабляются при infeasible результате.
- **Все выпуски** показывает компактный universe. Полная карточка загружается только после клика.

`analysis_status`, `safe_portfolio_eligible` и `opportunity_portfolio_eligible` независимы.

## Поддержанные структуры

- fixed bullet: YTM, duration, DV01, convexity, G/Z-spread;
- floater с подтверждённой формулой: projected coupons, Discount Margin, effective/spread duration;
- amortizing: fixed analytics и WAL;
- put/offer и callable: условные YTP/YTC, YTW;
- perpetual/resettable: current yield, условный YTC и extension proxy, без maturity YTM;
- index-linked/variable nominal классифицируются, но остаются `PARTIAL`, пока нет подтверждённой модели.

OAS всегда `null`/`UNSUPPORTED`, пока нет калиброванной стохастической модели ставок и встроенного опциона.

## Scenario Lab

Матрица строится Python-кодом и публикуется в detail payload. Текущая версия использует локальную one-year duration/convexity sensitivity с независимыми shocks кривой и спреда, 30 б.п. издержек и breakeven combined shock. Это sensitivity, а не прогноз и не full revaluation. Браузер только отображает готовые числа.

## Обновление

`python bonds/update_bonds.py` строит legacy artifacts, Bond Portfolio Lab v3 и v4 в одном validation gate. При ошибке v4 предыдущие валидные portfolio artifacts сохраняются. Daily workflow публикует `site/bonds/` рекурсивно, включая lazy details.

## Ограничения

- Полный статус возможен только при подтверждённых расписаниях и существенных ценах option exercise.
- Для части выпусков MOEX не предоставляет формулу флоатера, call/put price или юридические признаки в машиночитаемом виде.
- Bid/ask depth не моделируется при отсутствии фактического стакана; slippage calculator не подменяется surrogate.
- Net return является оценкой с явно указанными assumptions, не персональным налоговым расчётом.

Не является индивидуальной инвестиционной рекомендацией.
