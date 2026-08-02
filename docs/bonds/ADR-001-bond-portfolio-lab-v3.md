# ADR-001: Bond Portfolio Lab 3.0

## Status

Accepted for implementation. The legacy screener and Bond Finder remain available
until the v3 artifacts pass their quality gate.

## Decisions

### One normalized universe

`site/bonds/universe.json` is the only normalized source for portfolio presets,
the screener, relative-value diagnostics and market charts. Source-specific fields
are retained, but presentation code does not reinterpret them.

### Server-side MILP

Preset portfolios are solved in Python with `scipy.optimize.milp`. Binary issue and
issuer variables enforce cardinality and minimum-position constraints. A continuous
LP is not used as a substitute because it cannot enforce those constraints.

### Static-site interaction

GitHub Actions computes the 3 profile x 5 horizon matrix. Changing only the budget
runs deterministic integer-lot allocation in the browser against the selected target
composition. Advanced constraints are read-only in v3.0 until a pinned local WASM
MILP solver passes parity tests against Python. The UI must not pretend to optimize
custom constraints.

### Quality gate and last valid result

Critical source, schema, rating, duration and liquidity checks run before publication.
On failure the pipeline keeps `portfolio_last_valid.json`, publishes diagnostics in
`portfolio_validation.json`, and labels the composition as previous rather than new.

### Compatibility

Legacy `screener.json`, `chart_data.json`, `portfolios.json` and `finder.json` are not
removed in the first release. The frontend can fall back to them when v3 artifacts are
missing or invalid.
