# ISS Schema Notes — MOEX Bond Finder

Every endpoint used by `bonds/bond_finder.py`, verified **live** (2026-07-02) before any logic was
written against it. Format: endpoint → fields relied on → discrepancies vs spec assumptions.

## Reference-code discrepancy (spec-level)

The build spec references `bond_screener_v2.py` and an existing Aiogram 3.x bot. **Neither exists in
this repository** (verified: no file, no `aiogram` imports). This repo is a static GitHub Pages site
with GitHub Actions generators. Adaptation (approved pattern in this project): Python package + CLI +
rich → generator script in the heavy-deps workflow; Telegram alerts → "События" feed on the site;
SQLite journal/cache → repo-committed JSON journal; HTML report → the site's Bonds tab section.
Reference logic absorbed instead from `bonds/update_bonds.py` (v2 screener of this repo: G-curve,
cashflow YTM, bondization-based floater/amort detection, tax layer).

## 1. Boards

`GET /iss/engines/stock/markets/bonds/boards.json?iss.meta=off`

- Fields: `boardid`, `title`, `is_traded`.
- Verified: 12 traded boards (TQCB, TQOB, TQOD, TQOE, TQOY, TQRD, TQUD, AUBB, AUCT, PACT, PAYT, SPOB).
- Discovery is dynamic — a new traded board is picked up with zero code changes.
- Auction/placement boards (AU*, PACT, PAYT, SPOB) carry no usable secondary marketdata and fall out
  of the cheap filter naturally (no price/yield), not by hardcoded exclusion.
- **ПИР note:** boards `TQRD`/`TQUD` ("Облигации Д") are the Д-admission sector. They are excluded
  cheaply as a proxy, **and** every fine-stage candidate is additionally checked via the
  security-level `HIGHRISK` flag (see §4) — so the exclusion is ultimately field-based, as the spec
  prefers.

## 2. Securities per board

`GET /iss/engines/stock/markets/bonds/boards/{board}/securities.json?iss.meta=off`

- **Pagination discrepancy (major, inherited from v2):** the endpoint returns the whole board at
  once and **ignores `start=`** (verified: start=0 and start=100 return identical 3006 rows on
  TQCB). Loop must terminate on "no new SECIDs", otherwise it spins forever.
- `securities` block fields used: `SECID, SHORTNAME, SECNAME, BOARDID, PREVPRICE,
  PREVLEGALCLOSEPRICE, FACEVALUE, FACEUNIT, LOTVALUE, ACCRUEDINT, COUPONVALUE, COUPONPERCENT,
  COUPONPERIOD, NEXTCOUPON, MATDATE, OFFERDATE, PUTOPTIONDATE, CALLOPTIONDATE, BUYBACKDATE,
  ISSUESIZE, ISSUESIZEPLACED, LISTLEVEL, BONDTYPE, SECTYPE`.
- `marketdata` block fields used: `SECID, LAST, WAPRICE, MARKETPRICE, LCLOSEPRICE, VALTODAY,
  DURATION, YIELD, YIELDATWAPRICE, YIELDTOOFFER`.
- **Date placeholder discrepancy:** empty dates arrive as the string `"0000-00-00"`, not null
  (`has_date()` guard required — `BUYBACKDATE` is `"0000-00-00"` on virtually every bond).
- **`YIELD` staleness discrepancy:** marketdata `YIELD` is sometimes stale/wrong (verified in v2:
  liquid AAA showing 6% at par). Used only for the *cheap* pre-rank; the fine stage recomputes YTM
  from real cashflows at `WAPRICE`-based dirty price.
- `FACEUNIT`: RUB bonds are `SUR` (also accept `RUB`/`RUR`). FX issues (USD/EUR/CNY, incl.
  "Валютные облигации" mixed into TQCB) are excluded and **counted** in warnings.
- Issue size in RUB = `ISSUESIZEPLACED (fallback ISSUESIZE) × FACEVALUE`.

## 3. Benchmark curve (KBD/ZCYC)

`GET /iss/engines/stock/zcyc.json?iss.meta=off[&date=YYYY-MM-DD]`

- Block `yearyields`: `(tradedate, tradetime, period_years, value_pct)` — 11 tenors 0.25y…30y.
- Historical `date=` **verified working**: `date=2025-06-02` → 0.25y = 20.0873 % (era-plausible).
- `params` block is MOEX's own model (B1..B3, T1 + G1..G9) — **not** textbook NSS; we interpolate
  the published `yearyields` instead of re-deriving the formula (discrepancy vs any NSS assumption).
- Fallback when ZCYC block is empty: linear curve from TQOB (OFZ) yields by duration, with a loud
  warning attached to the run.

## 4. Per-security description (fine stage, 1 request/bond)

`GET /iss/securities/{secid}.json?iss.meta=off&iss.only=description`

- Rows are `(name, title, value)` triplets; parsed into a dict by `name`.
- **`HIGHRISK` = 1 → сектор ПИР** (verified on a TQRD bond: `HIGHRISK=1`; absent on regular TQCB
  issues). This is the real security-level ПИР marker the spec asked to find.
- **`ISQUALIFIEDINVESTORS`** (0/1) — qualified-investor flag, verified present.
- **INN discrepancy:** the description block of exchange bonds exposes emitter id fields
  inconsistently; we take `INN` when present, else `EMITTER_ID`, else fall back to issuer title
  with a warning (spec's fallback path).
- `LISTLEVEL` duplicated here; board value used first, description as fallback.

## 5. Coupon schedule / amortization

`GET /iss/securities/{secid}/bondization.json?iss.meta=off&limit=unlimited`

- Blocks: `coupons` (`coupondate, value, valueprc`), `amortizations` (`amortdate, value,
  facevalue`), `offers`.
- Floater detection: future known coupon `valueprc` not all equal, or `COUPONPERCENT` empty with
  non-fixed `BOND_TYPE` ("Флоатер" seen live in description).
- **Amortization discrepancy (as spec suspected):** the final redemption IS represented as the last
  `amortizations` row — a bond is "amortized" only when there is **>1** row.
- Coupons already paid (date ≤ today) are used by the journal review to add realized coupon income.

## 6. Liquidity / own history (fine stage, 1 request/bond)

`GET /iss/history/engines/stock/markets/bonds/boards/{board}/securities/{secid}.json?iss.meta=off&sort_order=desc&limit=130`

- Fields: `TRADEDATE, VALUE (RUB volume), CLOSE, WAPRICE, YIELDCLOSE, DURATION, ZSPREAD, ACCINT`.
- Liquidity = **median** of `VALUE` over the last 20 sessions (median, not mean, per spec).
- New placement = fewer than 20 sessions of history → skips liquidity filter, own report section.
- Cheapening signal: ISS publishes **historical `ZSPREAD`** right in bond history. When populated
  for ≥60 of the last ~130 sessions, today's percentile is computed against that series directly
  (more precise than re-deriving G-spread from weekly curve snapshots). When absent → G-spread
  history from `YIELDCLOSE` minus ZCYC interpolated at historical `DURATION` using **weekly** curve
  snapshots (≤26 extra requests per run, shared across bonds); if still <60 obs → per-bond note,
  no bonus (never fails the run).
- History pagination honors `limit=` (verified; unlike the securities endpoint).

## 7. Index benchmark (validation loop)

`GET /iss/history/engines/stock/markets/index/boards/RTSI/securities/RUCBTRNS.json`

- **Board discrepancy:** RUCBTRNS (and RGBITR, and MCFTR) live on board **`RTSI`**, not SNDX —
  SNDX returns an empty set (verified: RTSI → 2026-07-01 close 205.85). Same trap as the equity
  total-return index in this repo.
- Used by the journal review: index total return over each pick's holding window.

## Request budget

Monthly full run ≈ 12 board requests + 1 ZCYC + (3 × 60) fine-stage requests + ≤26 weekly curve
snapshots + 1 index ≈ **220 requests**, throttled (3 workers, retry/backoff 6). Daily change scan
uses the board listing + stored previous universe only (≈13 requests, no per-bond enrichment).
