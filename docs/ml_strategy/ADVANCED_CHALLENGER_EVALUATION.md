# Advanced challenger evaluation

The production champion remains the existing ElasticNet pipeline. Advanced
challengers run in a separate scheduled research workflow and cannot replace the
champion automatically.

`PRODUCTION_CHAMPION` means that ElasticNet is the current portfolio model, not
that its absolute performance is strong. The current common-window result has
negative after-cost CAGR and low Sharpe, so the public assessment is
`WEAK_NEEDS_IMPROVEMENT`.

## Candidates

### ElasticNet + ICEEMDAN features

The decomposition follows the recursion in Colominas et al. `iceemdan.m`.
PyEMD supplies the underlying EMD operator; the code does not call a CEEMDAN
alias. A committed reference fixture was generated from the original MATLAB
implementation with GNU Octave and the Rilling/Flandrin EMD implementation.

The numerical gate checks:

- reconstruction error;
- deterministic output for a fixed noise matrix and seed;
- mode count;
- correlation and energy of the leading modes against the reference;
- point-in-time feature construction using history available at each snapshot.

The candidate is the same ElasticNet model with six additional decomposition
features. It is not described as a standalone ICEEMDAN model.

### PatchTST

Input shape is `[batch, channel, lookback]`. Each channel is patched with
`Tensor.unfold`, projected with a shared linear embedding, combined with learned
positional encodings, and processed by a shared
`torch.nn.TransformerEncoder`. Channels are independent through the encoder and
join only in the prediction head.

Each walk-forward fold has:

- a scaler fitted only on the training portion of that fold;
- targets ending strictly before the prediction date;
- sequences ending no later than their sample date;
- a deterministic seed;
- a saved checkpoint and epoch history;
- OOS predictions saved separately from latest inference.

## Comparison contract

Promotion uses only the intersection of `(prediction date, ticker)` rows across
ElasticNet, ElasticNet + ICEEMDAN features, and PatchTST. Actual targets and
forward total returns must also match exactly. All candidates use the same
target, rebalance dates, one-way transaction cost, holdings count, turnover cap,
and portfolio construction.

The full-history ElasticNet result remains visible separately. It is not used to
give the baseline a longer comparison window in the promotion gate.

The artifact contains prediction metrics, after-cost portfolio metrics,
calendar-year metrics, execution metadata, checkpoint hashes, training history,
and OOS prediction hashes.

The full artifact is retained as a private GitHub Actions research artifact. The
GitHub Pages version is a separate allowlisted projection containing governance,
aggregate metrics, promotion decisions, and integrity flags. It excludes
checkpoints, fold-level records, datasets, predictions, machine paths, and
secrets.

## Statuses

Only these values are valid:

- `NOT_IMPLEMENTED`
- `IMPLEMENTED_NOT_EVALUATED`
- `EVALUATED_REJECTED`
- `EVALUATED_APPROVED`
- `PRODUCTION_CHAMPION`
- `EXECUTION_FAILED`

`production_evaluation` fails if any model is untrained, uses a mock backend,
lacks a checkpoint, or has no OOS predictions.

## Workflows

`.github/workflows/ml_strategy_challengers.yml` performs weekly retraining and
publishes only the comparison artifact. Daily and monthly production workflows
continue to build the existing ElasticNet snapshot and preserve the latest
advanced comparison. A challenger that passes the research gate still requires
a separate reviewed production change.
