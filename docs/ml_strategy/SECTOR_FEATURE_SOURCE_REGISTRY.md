# Sector Feature Source Registry

Version: `2026-07-27`

The machine-readable registry is `config/ml_strategy/sector_sources.yml`. It is JSON-compatible YAML so the bounded GitHub Actions runtime can parse it with the Python standard library.

## Approved series

| Series | Provider | Frequency | Point-in-time rule | Use |
|---|---|---:|---|---|
| `MOEX_USDRUB` | MOEX ISS | daily | official close is available after that close | oil/steel FX interaction |
| `MOEX_RGBI` | MOEX ISS | daily | official close is available after that close | banks/developers rate-cycle interaction |
| `CBR_KEY_RATE` | Bank of Russia | event | effective-date vintage, no backfill before effective date | banks/developers |

## Blocked series

- Audited Brent/Urals history: no stable licensed or official PIT feed is configured.
- Audited steel/input prices: no stable licensed PIT feed is configured.
- CBR mortgage vintages: the public source exists, but vintage-aware ingestion is not implemented.

Blocked sources never receive placeholder values. A failed required source blocks that pack snapshot and preserves the previous valid pack; it does not break the base ML strategy.
