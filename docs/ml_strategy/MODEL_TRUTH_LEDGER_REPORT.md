# Model Truth Ledger - Stage 3A

## Scope

Stage 3A adds an append-only public record for every forecast published in the
model portfolio. The record is separate from the historical backtest and is
labelled `SHADOW_LIVE`; it is not a production performance claim.

Each forecast stores:

- creation time and point-in-time data cutoff;
- ticker and 20-session horizon;
- point excess-return forecast and cross-sectional rank bucket;
- model, feature and dataset versions;
- portfolio target/change weights;
- previous record hash and content hash.

Prediction intervals, confidence and regimes remain explicitly unavailable
until Stages 3B and 3C. No values are inferred or filled for presentation.

## Storage And Integrity

The daily pipeline publishes:

```text
data/ml_strategy/ledger/index.json
data/ml_strategy/ledger/open/YYYY-MM-DD.json
data/ml_strategy/ledger/resolved/YYYY-MM-DD.json
```

The same files are copied to `site/ml_strategy/ledger/`. The index retains all
records and resolutions. Daily files are rebuilt from that full valid index so
the GitHub Pages replacement step cannot discard older dates.

Forecast IDs are deterministic for a data cutoff, ticker and model bundle
version. Re-running the same cutoff neither appends duplicates nor changes the
chain head. A changed forecast for an existing ID stops the run. Validation is
completed in a temporary directory before the last valid snapshot is replaced.

The SHA-256 chain detects accidental or deliberate history edits. It is not a
digital signature and does not prove who produced a record.

## Automatic Resolution

An open record is resolved only after 20 later MCFTR trading sessions exist.
Realized return uses cached official MOEX close history and adds an official
MOEX dividend when a matching record is available. The benchmark is MCFTR.
Missing start/end prices leave the record open rather than inventing a value.

Resolution publishes realized total and excess return, direction accuracy,
forecast rank bucket, interval coverage when an interval exists, portfolio
contribution and the configured one-way cost estimate.

## Public Metrics

The frontend displays open/resolved counts, directional accuracy, live
Spearman IC and interval coverage. Metrics that require later stages remain
`null` until there is a valid input:

- conformal coverage and interval width: Stage 3B;
- calibration by confidence and abstention rate: Stage 3B;
- hit rate by regime: Stage 3C;
- false-positive rebalance rate: Stage 3E.

At least 20 resolved forecasts are required before the ledger status changes
from `INSUFFICIENT_HISTORY` to `LIVE`.

## Portfolio Plan And Sector Context

The public portfolio translates target weights into an explicit `BUY`, `SELL`
or `HOLD` plan using the previously published share count, current official
close and lot-rounded target shares. Re-running the same data cutoff preserves
the original current portfolio, so a repeated workflow cannot turn an existing
trade plan into a misleading no-action result.

Previous holdings outside the new top-N remain in the optimizer universe and
are shown as explicit reductions rather than silently disappearing.

Available sector drivers are displayed beside relevant securities. A
`RESEARCH_ONLY` pack is labelled `context only` and does not affect forecasts
or weights. It can enter production only after the pre-declared forecast and
after-cost ablation gates pass.

## Migration

There was no public ledger before Stage 3A. The first successful deployment
starts the chain from the genesis hash and does not backfill historical
forecasts from backtests. Backtest observations must never be relabelled as
live records.

Future corrections must create a new forecast record with an explicit
supersession reference. Existing records and resolutions must not be edited.

## Incident And Rollback

If integrity validation fails:

1. stop publication and preserve the last valid site snapshot;
2. archive the failing workflow logs and candidate JSON;
3. compare the candidate `previous_record_hash` with the public chain head;
4. identify whether the input cutoff, model version or dataset fingerprint
   changed;
5. fix the producer and rerun from the last valid index;
6. never remove or rewrite an unfavourable resolved forecast.

To roll back application code, revert the Stage 3A commit and redeploy the last
valid site version. Preserve `data/ml_strategy/ledger/` in the workflow cache.
Do not restore a ledger from a backtest output or regenerate old forecasts with
a newer model.

## Known Limitations

- The first deployment has no resolved live observations.
- MOEX dividend resolution depends on the official dividend cache being
  available; otherwise price return is retained and the method remains
  disclosed.
- Hash chaining provides tamper evidence, not cryptographic authorship.
- Forecasts cover published model-portfolio positions, not every security
  evaluated internally during model selection.
