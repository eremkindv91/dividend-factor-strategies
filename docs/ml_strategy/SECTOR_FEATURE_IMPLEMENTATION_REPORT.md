# Sector Feature Implementation Report

Date: `2026-07-27`

## Implemented

- Versioned source registry, release-lag policy, feature flags and issuer-sector mapping.
- Observation contract with `period_start`, `period_end`, `published_at`, `available_at`, `ingested_at`, revision flags and source identifiers.
- As-of merge that only exposes an observation when `available_at <= prediction_timestamp`.
- Four priority packs:
  - `OIL_AND_GAS`
  - `STEEL_AND_FERROUS_METALS`
  - `BANKS_AND_FINANCIALS`
  - `REAL_ESTATE_DEVELOPERS`
- Fixed Ridge ablation on the same folds, universe, target, execution assumptions, optimizer constraints and costs.
- Production promotion only after forecast and after-cost gates.
- Separate registry and quality snapshots under `ml_strategy/sector_features/`.
- Compact UI status for all packs; stock-level drivers are rendered only for approved packs.

## Feature set

The current auditable candidates are interactions of a sector identifier with official USD/RUB, RGBI and Bank of Russia key-rate data. They do not claim to represent oil, steel or mortgage fundamentals.

Issuer-specific exposures are `BLOCKED`. The mapping file intentionally contains no guessed production weights.
