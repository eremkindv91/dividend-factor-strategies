# Verified bond terms

One JSON file per canonical SECID. Records must cite a public issuer, prospectus or official
disclosure URL and contain `source_date`, `verified_at` and `terms_version`. Missing or unverified
terms are not inferred: the relevant v4 analysis remains `PARTIAL`.

Golden fixtures belong in `tests/fixtures/`, not in this production registry.
