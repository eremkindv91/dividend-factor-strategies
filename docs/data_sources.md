# Data Sources

This file documents curated source pages used by the additive financial data
pipeline. A row in `data/company_sources.csv` is not a financial fact by itself:
it is a verified official entry point for future report discovery and extraction.

## Verified Official Source Pages

Verified on 2026-06-28 with metadata-only HTTP checks. These rows are stored as
`source_type=financial_reports_page`, `document_type=financial_reports_page`,
`reporting_standard=MIXED` because the pages can contain annual, interim, IFRS,
RAS, presentations, or archive links.

| Ticker | Source |
| --- | --- |
| CHMF | https://severstal.com/eng/ir/indicators-reporting/financial-results/ |
| GMKN | https://nornickel.com/investors/disclosure/financials/ |
| LKOH | https://www.lukoil.com/InvestorAndShareholderCenter/FinancialReports |
| MGNT | https://www.magnit.com/en/shareholders-and-investors/results-and-reports/ |
| MOEX | https://www.moex.com/s1355 |
| NLMK | https://nlmk.com/en/ir/results/ |
| NVTK | https://www.novatek.ru/en/investors/results/ |
| PHOR | https://www.phosagro.com/investors/reports/msfo/ |
| PLZL | https://polyus.com/en/investors/results-and-reports/ |
| ROSN | https://www.rosneft.com/Investors/Reports_and_presentations/Consolidated_financial_statements/ |
| SBER | https://www.sberbank.com/investor-relations/groupresults |

## Excluded From This Seed

The following candidate pages were not committed because the metadata check did
not confirm a usable official source page in this run:

| Ticker | Reason |
| --- | --- |
| GAZP | Request timed out. |
| TATN | Request timed out. |
| ALRS | Candidate URL returned 404. |
| MTSS | Candidate URL redirected to an error page. |

## Publication Rule

The site can show these sources as coverage metadata. It must not treat a source
page as a reliable financial value until a specific report/table/value is
extracted with provenance, quality score, units, period, and conflict checks.
