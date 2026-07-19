#!/usr/bin/env python3
"""Discover dividend-decision document links for manual verification.

This is intentionally not part of the daily public calendar build.  It makes a
small, rate-limited pass over already configured issuer/disclosure pages and writes
only candidates to the ignored manual-review queue.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data_sources import disclosure_adapter as disclosure  # noqa: E402
from src.io_utils import utc_now_iso  # noqa: E402


REPO = Path(__file__).resolve().parents[1]
OUTPUT_COLUMNS = [
    "ticker", "inn", "company_name", "source_url", "document_title", "candidate_status",
    "discovery_status", "checked_at",
]


def discover(root: Path, network: bool, limit: int) -> tuple[list[dict], list[dict]]:
    sources = disclosure.load_report_sources(root)
    pages = sources[sources["source_type"].isin(disclosure.PAGE_SOURCE_TYPES)] if not sources.empty else sources
    seen, candidates, errors = set(), [], []
    for _, source in pages.iterrows():
        row = source.to_dict()
        ticker = str(row.get("ticker") or "").upper()
        if ticker in seen or (limit and len(seen) >= limit):
            continue
        seen.add(ticker)
        url = str(row.get("source_url") or "").strip()
        if disclosure.validate_source_url(url):
            errors.append({"ticker": ticker, "source_url": url, "issue": "invalid_source_url"})
            continue
        if not network:
            continue
        meta = disclosure.safe_metadata_check(disclosure.check_url_metadata, url)
        if not meta.get("ok") or not disclosure.looks_like_html(meta.get("content_type"), url):
            errors.append({"ticker": ticker, "source_url": url, "issue": "page_unavailable_or_not_html"})
            continue
        try:
            html = disclosure.fetch_page_html(meta.get("final_url") or url)
        except Exception:
            errors.append({"ticker": ticker, "source_url": url, "issue": "page_fetch_error"})
            continue
        if disclosure.is_disclosure_error_page(html):
            errors.append({"ticker": ticker, "source_url": url, "issue": "waf_or_error_page"})
            continue
        candidates.extend(disclosure.discover_dividend_decision_links(html, meta.get("final_url") or url, row, utc_now_iso()))
        time.sleep(0.4)
    return candidates, errors


def write_rows(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", action="store_true", help="perform a bounded metadata/page check")
    parser.add_argument("--limit", type=int, default=30, help="maximum unique issuers to inspect")
    parser.add_argument("--out", default=str(REPO / "data" / "manual_review" / "dividend_disclosure_candidates.csv"))
    args = parser.parse_args(argv)
    candidates, errors = discover(REPO, args.network, args.limit)
    write_rows(Path(args.out), candidates, OUTPUT_COLUMNS)
    print(f"[dividend-disclosure] candidates={len(candidates)} errors={len(errors)} network={args.network}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
