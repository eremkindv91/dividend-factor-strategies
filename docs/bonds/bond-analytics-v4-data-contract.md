# Bond Analytics v4 data contract

## Public artifacts

| Path | Purpose |
| --- | --- |
| `site/bonds/analytics_manifest.json` | Counts, curve provenance, hashes and lazy-detail contract |
| `site/bonds/universe_v4.json` | Compact rows for filters and tables |
| `site/bonds/details/<SECID>.json` | Lazy structure, metrics, cash flows, scenarios and provenance |
| `site/bonds/portfolio_safe_v4.json` | Pointer confirming unchanged v3 Safe semantics |
| `site/bonds/portfolio_opportunities.json` | Precomputed allocations by profile/budget/access/complex switch |

## Stability

- `schema_version` is `4.0`.
- JSON is serialized with `allow_nan=false`.
- Missing analytics is `null` or an explicit status, never a fake zero.
- Legacy `bond_structure_type` remains in compact rows for compatibility; decisions use the compositional `structure` object in detail files.
- Initial page load fetches compact artifacts only. It must not fan out over `details/`.

## Structure

The detail payload composes:

- `coupon_model`;
- `principal_model`;
- `optionality`;
- `seniority`;
- access/legal flags;
- computed `capabilities`.

`FULL` means the structure-specific engine received the required price, terms and schedule. `PARTIAL` means the issue is visible but a metric cannot be published reliably. `UNSUPPORTED` means no implemented engine can represent the structure.

## Metrics

Each metric includes `value`, `unit`, `method`, `as_of`, `status` and relevant inputs. Relative-value comparisons are made inside structure/rating/duration peer groups. Opportunity score factors and weights are published for explanation.

## Eligibility invariants

- Safe mode never gains complex bonds because v4 exists.
- Opportunities require `FULL`, relative-value support, rating and liquidity.
- Qualified-only and complex structures require explicit switches.
- Infeasible allocations publish no positions, zero invested amount and the full budget as cash.
- OAS is not supported or inferred from a simple spread.

## Provenance and security

Public JSON contains logical sources and URLs only. It must not contain local absolute paths, secrets, cache files or raw reports. Validation is performed by `python scripts/validate_site_data.py bonds`.
