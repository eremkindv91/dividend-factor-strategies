# Base ML Strategy: deployment report

## Release

- Source branch: `dividend-site`
- Feature commit: `099dca907a03728d9e24c2fae5ace8d5bbf4fa79`
- Runtime fix: `4dcc1f6c023dfee8d26d06c8e6c69a938a0394ee`
- Main site deploy: run `30300240792`, success
- ML snapshot deploy: run `30302615644`, success
- Public URL: <https://eremkindv91.github.io/dividend-factor-strategies/#strategies>
- Data as of: `2026-07-24`
- Snapshot generated at: `2026-07-27T20:49:00+00:00`

The first ML run `30300243059` stopped before publication because the bounded
GitHub Actions runtime did not declare the `ta` package required by the existing
market-history collector. Commit `4dcc1f6` added that direct dependency to the
daily, weekly, and monthly workflows. The failed run did not change `gh-pages`.

## Production snapshot

- Signal: `WATCH`
- Reason: first live snapshot; observations must accumulate before a rebalance
  decision is evaluated.
- Champion: `elastic_net`
- Model status: `APPROVED`
- Prediction gate: `APPROVED`
- Optimizer: constrained maximum Sharpe with lot, liquidity, turnover, sector,
  concentration, volatility, and beta constraints.
- Portfolio table: 12 candidates, 7 positive target weights.
- Cash weight: `64.13%`
- Turnover: `35.87%`
- Estimated one-way execution cost: `1,076.17 RUB` at `30 bps`
- Estimated annualized volatility: `7.37%`
- Estimated beta: `0.33`

`APPROVED` applies to the model gate, not to data quality and not to an
investment recommendation.

## Out-of-sample results

Purged walk-forward validation uses only training labels whose
`target_end_date < prediction_date`. Execution starts on the next trading
session and includes the configured costs.

- Evaluation periods: `24`
- CAGR after costs: `-0.45%`
- Annualized volatility: `21.88%`
- Sharpe after costs: `0.081`
- Maximum drawdown: `-25.64%`
- Cumulative return after costs: `-0.86%`
- MCFTR cumulative return: `-6.02%`
- Cumulative excess return: `+5.16%`
- Average turnover: `40.0%`

The strategy beat MCFTR over this sample, but its absolute after-cost return and
Sharpe are weak. Stage 1 is therefore a research baseline, not evidence of a
stable investable edge.

## Data quality

Overall status: `DEGRADED`.

Passed checks:

- 238 MOEX ISS price series;
- 235 series with sufficient history;
- 5,860 MCFTR observations;
- 60 securities in the latest investable cross-section;
- official MOEX dividend records for 162 of 238 series;
- IMOEX, RGBI, USD/RUB, and Bank of Russia key-rate features;
- freshness within the seven-calendar-day limit.

Degraded checks:

- 8 split-like observations exceed the 60% daily-return threshold and are
  excluded rather than repaired by assumption;
- historical index membership is reconstructed from trading availability, but
  the security master is not a complete listing and delisting history.

PatchTST and ICEEMDAN remain `BLOCKED`. They are not presented as production
models without sufficient sequence infrastructure, an audited backend, and a
positive out-of-sample ablation.

## Verification

Pre-deploy:

- `pytest -q`: 568 passed, 1 skipped;
- `pytest -q market_saw/tests`: 44 passed;
- targeted ML and strategy UI tests: 13 passed;
- `python -m src.pipeline.run_all --skip-ocr`: passed;
- official IFRS audit: 200 facts, 0 missing;
- site JSON validation: passed;
- `node -c site/app.js`: passed.

Live:

- `latest.json`, `backtest.json`, `model_card.json`, and `data_quality.json`
  returned valid JSON;
- the ML tab rendered 12 portfolio rows and one out-of-sample chart;
- the public page loaded `app.js?v=099dca90`;
- no browser console errors were observed;
- Overview, Portfolio, Stocks, Strategies, News, Bonds, Banks, Methodology, and
  About opened with the expected route and page heading;
- desktop document and ML panel had no horizontal overflow.

Responsive layout was also verified locally at 390 x 844 before release. The
live browser viewport override was unavailable during the post-deploy run, so
the live mobile check should be repeated manually on a physical device.

## Rollback

The ML snapshot is published additively under `gh-pages/ml_strategy/`; existing
site data is not replaced. To roll back the feature:

1. revert commits `4dcc1f6` and `099dca9` on `dividend-site`;
2. run the normal site deploy;
3. remove `ml_strategy/` from `gh-pages` only if the public artifacts must also
   be withdrawn.

The validators use atomic replacement and preserve the last valid local bundle
when a new build fails.

## Stage 2 gate

Stage 2 may start only from this deployed baseline. A sector feature can enter
production only after its source, release lag, point-in-time mapping, missing
data behavior, and same-fold after-cost ablation are documented and tested.
