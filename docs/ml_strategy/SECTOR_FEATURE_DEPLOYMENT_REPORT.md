# Sector Feature Deployment Report

Date: `2026-07-27`

Source commit: `ff46ef3016a56a5d09fd7aaa64d3fca5d2c14039`

## GitHub Actions

- ML snapshot run: `30306671241` — success, `10m25s`.
- Full site deploy: `30307450585` — success.
- Public smoke-test in the full deploy passed.

## Live verification

- Cache-busted asset: `app.js?v=ff46ef30`.
- Public sector snapshot generated at `2026-07-27T21:31:43Z`.
- Four priority packs are visible.
- Approved production sector packs: `0`.
- Research-only sector packs: `4`.
- Approved sector feature columns: `0`.
- Issuer exposures: `BLOCKED`.
- Portfolio table: `12` rows.
- Desktop horizontal overflow: none.

The result is intentionally conservative. No pack passed both the fixed forecast gate and the after-cost portfolio gate, so the Stage 1 production feature set was preserved.
