# Bond Analytics Engine v4: audit

Дата аудита: 2026-08-24. Ветка: `redesign/market-dialog-analytics`.

## Baseline

- Bond regression: `86 passed`.
- Full Python suite: `1145 passed, 1 skipped`.
- Два baseline warning относятся к локальным версиям `numexpr` и `bottleneck`, а не к bond-коду.
- Публичный v3 universe содержит 857 выпусков: 288 vanilla fixed, 219 floating,
  159 offer, 150 amortizing, 21 complex, 9 index-linked и 11 unknown.

## Current contracts

- `bonds/update_bonds.py` строит legacy screener/chart/portfolios и затем вызывает
  `bonds.pipeline_v3.build_and_publish`.
- `site/bonds/universe.json` имеет schema `3.0`; safe allocator требует корректные dirty price,
  calculated modified duration и G-spread.
- `bonds/portfolio_config.json` по умолчанию исключает floaters, amortizing, put/offer,
  callable и qualified-only. Эти ограничения являются safety policy и не ослабляются v4.
- `bonds/universe_builder.py` классифицирует сложные структуры, но внутренний YTM намеренно
  рассчитывает только для complete fixed bullet cash flows.
- MOEX bondization уже загружается для каждого выбранного выпуска, но полный coupon/principal/
  offer schedule не сохраняется в публичных artifacts; доступен только агрегат на 12 месяцев.
- `site/app.js` содержит существующий safe Bond Portfolio Lab и карточку выпуска. Новый v4 UI
  должен быть отдельным модулем; router/mount остаётся в `app.js`.

## Gaps against v4

1. Одна legacy-классификация не описывает одновременно coupon, principal, optionality,
   seniority и access.
2. Нет общего typed cash-flow contract и dispatcher по структурам.
3. Нет CurveProvider с discount factors и provenance; Z-spread берётся как необязательное поле
   MOEX и не проверяется reverse pricing.
4. Нет Discount Margin, WAL, YTP/YTW, conditional YTC и perpetual/reset scenarios.
5. `analyzable`, Safe eligibility и Opportunities eligibility не разделены.
6. Relative value и objective v3 рассчитаны для fixed/G-spread; смешивать их напрямую с DM или
   conditional YTC финансово некорректно.
7. Нет compact v4 universe, manifest и lazy detail payloads.
8. Текущий detail UI статичен и не использует capability matrix.

## Backward compatibility plan

- Не менять v3 schema, safe config, `_eligible`, MILP и существующие published files.
- Строить v4 как additive output после успешной v3-сборки.
- V4 detail calculations читают реальные MOEX market/bondization inputs и verified terms registry.
- Unsupported или неполные условия дают `PARTIAL`/`UNSUPPORTED` и `null`, а не surrogate/zero.
- `supports_oas=false` до появления calibrated stochastic option model.
- Газпром К2 используется только в test fixture. Production-код не содержит его SECID или
  reference values.

## Data limitations

- MOEX ISS не всегда раскрывает contractual reference index, observation lag, floors/caps,
  seniority и coupon-deferral terms в структурированном виде. Без verified terms record такие
  выпуски остаются `PARTIAL`.
- Realtime order book отсутствует в static GitHub Pages; bid/ask и slippage не имитируются.
- Исторические spread percentiles публикуются только при достаточном реальном ряду.
- True OAS, stochastic call exercise и variable-nominal pricing являются P2 и не подменяются
  простыми spread differences.
