# ML Strategy: implementation audit

Date: 2026-07-27

## 1. Existing foundation

- The site is a static GitHub Pages application built from `site/index.html`,
  `site/styles.css`, `site/app.js`, and generated JSON snapshots.
- The Strategies section already contains RU Quality, Momentum, dividend
  revaluation, and classical portfolio optimizers.
- `scripts/daily_ingest.py` stores official MOEX ISS daily OHLCV in
  `data/daily/prices/*.parquet`. The store is incremental, atomic, cached by
  GitHub Actions, and retains last-good series on source failure.
- `scripts/moex_iss.py` provides official MOEX prices, OHLCV, lot sizes, and
  dividend records. `market_saw/shared/moex_index_fetch.py` provides official
  index history for MCFTR, IMOEX, and RGBI-compatible instruments.
- `data/security_master.json` provides ticker, board, sector, share class, and
  active status for the current Russian equity universe.
- The repository already has portfolio risk calculations, MOEX trading
  calendar utilities, JSON contract validation, cache busting, last-good
  deployment, and public smoke tests.

## 2. Reusable components

- MOEX ISS retry/rate-limit patterns and the daily parquet store.
- MCFTR as the total-return benchmark.
- Security master sectors and lot sizes.
- Static snapshot delivery and `dataURL()` cache busting.
- GitHub Actions cache and additive publishing to `gh-pages`.
- Existing responsive tab, table, status, and disclosure UI patterns.

## 3. Components to create

- A separate `ml_strategy` Python package for data contracts, point-in-time
  features, models, walk-forward evaluation, optimization, signals, and atomic
  snapshot publishing.
- Daily, weekly, and monthly Actions workflows.
- JSON schemas/gates for latest portfolio, backtest, model card, and data
  quality.
- The ML Optimizer strategy panel.
- Phase 2 publication-aware sector packs and their ablation registry.
- Phase 3 immutable forecast ledger, uncertainty/abstention, regime engine,
  model league, and counterfactual portfolio twin.

## 4. Real sources available

| Dataset | Source | Current use |
| --- | --- | --- |
| Equity OHLCV/value/volume | MOEX ISS | Features, liquidity, risk, execution |
| Dividends and record dates | MOEX ISS | Total-return target |
| MCFTR, IMOEX, RGBI | MOEX ISS | Benchmark and market regime |
| Trading calendar | MOEX calendar endpoints/local verified rules | Session alignment |
| Key rate and FX | Bank of Russia | Optional macro features after PIT gate |
| Fundamentals | Official issuer/IFRS layer | Excluded from Stage 1 unless `available_at` is present |

No generated values, inferred publication dates, or browser-side model
calculations are allowed in production.

## 5. GitHub Pages constraints

Pages cannot train or infer. Actions must produce complete versioned snapshots,
validate them, and publish them atomically. The browser only renders JSON.
Secrets must stay in Actions. A failed build must not overwrite the last valid
snapshot. Large parquet and model binaries remain in Actions cache, not Pages
or git.

## 6. Bias and leakage risks

- **Look-ahead:** features at `t` may use rows no later than `t`; targets must
  end before a later fold can train on them. Same-close execution is forbidden.
- **Fundamental timing:** a fiscal period is not availability. Fundamentals
  require explicit `published_at` and `available_at`.
- **Survivorship:** the current master is not a complete historical membership
  database. Stage 1 builds membership from actual trading availability on each
  date and reports the residual bias. Delisted names need a separately
  maintained historical security master before claiming a bias-free long
  backtest.
- **Corporate actions:** unresolved split-like moves are excluded rather than
  guessed. Dividend cash flows are included only from official records.
- **Selection/tuning:** model promotion uses purged out-of-sample folds and
  after-cost portfolio results, never final-test tuning.
- **Revisions:** macro and sector data need release-vintage policy before use.

## 7. Recommended tree

```text
ml_strategy/
  config.py
  data.py
  features.py
  models.py
  optimization.py
  pipeline.py
  schemas.py
scripts/
  build_ml_strategy.py
  validate_ml_strategy.py
data/ml_strategy/
  latest.json
  history/YYYY-MM-DD.json
  backtest.json
  model_card.json
  data_quality.json
site/ml_strategy/
  (published mirrors)
tests/ml_strategy/
docs/ml_strategy/
.github/workflows/
  ml_strategy_daily.yml
  ml_strategy_weekly.yml
  ml_strategy_monthly.yml
```

## 8. Stage 1 MVP architecture

1. Restore cached MOEX parquet and increment it from ISS.
2. Refresh official index/dividend caches.
3. Apply coverage, staleness, liquidity, extreme-move, and timestamp gates.
4. Build point-in-time market/liquidity/risk features.
5. Build a 20-session forward excess total-return target against MCFTR.
6. Run purged walk-forward baseline and ElasticNet evaluation.
7. Treat tree models as challengers; promote only after stable OOS improvement.
8. Shrink forecasts and construct HRP / constrained max-Sharpe portfolios with
   equal-weight fallback.
9. Apply costs, liquidity, sector, weight, turnover, lot, and cash constraints.
10. Validate all snapshots in a temporary directory, then replace last-good.

## 9. Deployment plan

- Stage 1: separate commit and deployment after unit, leakage, optimizer,
  schema, frontend, regression, and live smoke checks.
- Daily: data refresh, quality gates, inference, optimization, signal, additive
  snapshot publish.
- Weekly: drift and diagnostics; no automatic champion promotion.
- Monthly: retraining, complete walk-forward, challenger review, official
  model-portfolio snapshot.
- Full `update.yml` and manual deployment preserve the newest ML directory from
  last-good so unrelated site rebuilds cannot erase it.
- Rollback is the previous `gh-pages` commit plus cached last-good snapshots.

## 10. Acceptance criteria

- Real MOEX data produces a non-empty, validated `latest.json`.
- Feature timestamps and purged folds pass leakage tests.
- MCFTR is present; stale or incomplete data causes a blocked/degraded state,
  never a fabricated forecast.
- Zero, historical mean, momentum, ElasticNet, equal-weight, minimum-variance,
  HRP, max-Sharpe, and equal fallback are measured or explicitly statused.
- Portfolio weights satisfy long-only, sum, security, sector, concentration,
  turnover, liquidity, lot, and cash rules.
- Model and portfolio metrics are after explicit costs.
- The live desktop/mobile panel loads all snapshots and states limitations.
- Existing site tabs and tests remain green.
- Each later phase is deployed only after the previous live deployment passes.
