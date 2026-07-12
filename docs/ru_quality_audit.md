# RU Quality: audit of the production inputs

Audit date: 2026-07-12. Branch baseline: `dividend-site` at `2849035`.

## Existing implementation

- `scripts/build_valuations.py::attach_quality_barra` was a legacy proxy: accounting ROE, CV of EBITDA/net profit, and Debt/EBITDA. It neutralized each descriptor by sector de-meaning and published hard-coded historical claims in the frontend.
- The legacy calculation is retained for one compatibility release as `quality_ru_legacy` / `ru_quality_legacy_v1`, with `quality_barra` as a deprecated alias. It is not used by the new RU Quality portfolio.
- The portfolio constructor is static-site JavaScript. Cross-sectional factor calculations belong in Python and are published as JSON.

## Available fundamentals

- Main panel: `data/panels_final/panel_russia_final.csv`.
- Shape at audit: 3,048 rows, 268 tickers, 2011-2025, 14 sectors.
- Core accounting fields: net profit (2,973 rows), equity (3,009), total debt (1,500), market cap (2,608), year-end price (2,836).
- Five annual observations: net profit and equity for 243 companies; total debt for 119 companies.
- `data/official_ifrs_facts.csv`: 200 verified facts, 15 tickers, ten canonical line items. It does not contain EPS, share counts, or publication dates.

## Missing strict inputs

- No reported EPS/TTM EPS in the production panel.
- No weighted-average diluted shares or reliable historical shares outstanding.
- No split/reverse-split adjustment chain attached to EPS history.
- No `publication_date`, `ingestion_date`, consolidated flag, or restatement flag in the panel.
- `data/report_index.parquet` is generated in the disclosure pipeline, but the audited snapshot had 21 rows with blank publication dates.

Consequences:

- strict `EPS_TTM / BVPS` and five-year EPS variability cannot currently be produced for the live universe;
- accounting `net_profit / equity` may be shown only as an explicit ROE fallback;
- a strict point-in-time backtest must remain unavailable;
- rows without confirmed publication dates are low confidence and are excluded from the default portfolio.

## Financials

- `Финансы (Банки)`: 15 tickers, including SBER/SBERP, T/TCSG, VTBR and BSPB/BSPBP.
- `Финансы`: 13 tickers, including MOEX, DOMRF, LEAS, SFIN, RENI and RGSS.
- Industrial Debt/Equity is not applied to either group. They receive `sector_specific_model_required` until a separate bank/financial model has sufficient dated source coverage.

## Update workflows

- `.github/workflows/refresh.yml` refreshes the SmartLab panel and forecast artifact quarterly.
- `.github/workflows/update.yml` runs the daily disclosure/unified financial pipeline and publishes GitHub Pages.
- `.github/workflows/update_financial_data.yml` validates the safe financial pipeline daily and on relevant pull requests.
- `scripts/build_quality.py` is placed after financial validation/unification and before `build_data.py`; a second pass hydrates current price/lot metadata after `data.json` is built.

## Methodological references

- MSCI Quality Indexes Methodology (May 2022): public three-variable score, missing rules, weighting, semiannual review and sector-neutral construction.
- MSCI Fundamental Data Methodology (June 2024): public fundamental-data availability and treatment principles.
- MSCI FaCS Methodology: public extended quality-pillar research reference.
- No proprietary Barra exposures, licensed index data, or protected methodology text are used.
