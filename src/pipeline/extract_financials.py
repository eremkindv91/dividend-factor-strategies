from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.io_utils import stable_id, utc_now_iso, write_csv, write_parquet
from src.normalization.ifrs_mapper import load_ifrs_mapping, map_line_item
from src.normalization.numeric import normalize_numeric_value
from src.paths import REPO_ROOT


IFRS_COLUMNS = [
    "fact_id", "report_id", "ticker", "inn", "company_name", "fiscal_year", "period",
    "period_type", "reporting_standard", "statement_type", "line_item_raw", "line_item_std",
    "value_raw", "value_normalized", "currency", "unit_type", "unit_multiplier", "is_calculated", "formula",
    "source_page", "source_table_id", "source_url", "source_name", "source_type", "source_priority",
    "is_legacy_data", "extraction_method", "confidence_score", "quality_score",
    "validation_status", "created_at",
]


LOW_CONF_COLUMNS = ["report_id", "ticker", "line_item_raw", "confidence_score", "created_at"]


def extract_financials(root: Path = REPO_ROOT, skip_ocr: bool = True) -> dict:
    path = root / "data" / "processed" / "ifrs_financial_facts.parquet"
    report_index = root / "data" / "report_index.parquet"
    mapping_path = root / "data" / "mapping" / "ifrs_line_items.yaml"
    if not mapping_path.exists():
        mapping_path = REPO_ROOT / "data" / "mapping" / "ifrs_line_items.yaml"
    mapping = load_ifrs_mapping(mapping_path)
    now = utc_now_iso()
    facts: list[dict] = []
    low_conf: list[dict] = []
    reports_extracted = 0
    reports_failed = 0

    if report_index.exists():
        reports = pd.read_parquet(report_index)
        for _, report in reports.iterrows():
            file_path = str(report.get("file_path") or "").strip()
            if not file_path:
                continue
            full_path = root / file_path
            try:
                extracted = extract_structured_report(root, report.to_dict(), full_path, mapping, now)
            except Exception as e:  # noqa: BLE001
                reports_failed += 1
                low_conf.append({
                    "report_id": report.get("report_id"),
                    "ticker": report.get("ticker"),
                    "line_item_raw": "",
                    "confidence_score": 0,
                    "created_at": f"{now} extraction_error={e}",
                })
                continue
            if extracted:
                reports_extracted += 1
                facts.extend(extracted)

    out = pd.DataFrame(facts, columns=IFRS_COLUMNS) if facts else pd.DataFrame(columns=IFRS_COLUMNS)
    write_parquet(path, out)
    write_csv(root / "data" / "manual_review" / "extraction_low_confidence.csv", low_conf, LOW_CONF_COLUMNS)
    return {
        "reports_extracted": reports_extracted,
        "reports_failed": reports_failed,
        "facts_from_ifrs": int(len(out)),
        "ocr_skipped": skip_ocr,
        "mode": "structured_files_only",
    }


def extract_structured_report(root: Path, report: dict, file_path: Path, mapping: dict, now: str) -> list[dict]:
    if not file_path.exists():
        raise FileNotFoundError(str(file_path))
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(file_path)
    elif suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(file_path)
    else:
        return []

    required = {"line_item_raw", "value"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{file_path.relative_to(root)} missing columns: {sorted(missing)}")

    rows: list[dict] = []
    for _, rec in df.iterrows():
        raw = str(rec.get("line_item_raw") or "").strip()
        canonical = str(rec.get("line_item_std") or "").strip() or map_line_item(raw, mapping)
        if not raw or not canonical:
            continue
        spec = mapping.get(canonical, {})
        multiplier = rec.get("unit_multiplier", 1)
        unit_type = rec.get("unit_type") or spec.get("unit") or "currency"
        value_raw = rec.get("value")
        value_normalized = normalize_numeric_value(value_raw, multiplier, spec.get("sign_convention", "as_reported"))
        if value_normalized is None:
            continue
        fiscal_year = _int_or_none(report.get("fiscal_year"))
        period = str(report.get("period") or fiscal_year or "")
        ticker = str(report.get("ticker") or "").strip().upper()
        fact_id = stable_id("official_ifrs", report.get("report_id"), ticker, period, canonical, raw)
        rows.append({
            "fact_id": fact_id,
            "report_id": report.get("report_id"),
            "ticker": ticker,
            "inn": report.get("inn"),
            "company_name": report.get("company_name"),
            "fiscal_year": fiscal_year,
            "period": period,
            "period_type": report.get("period_type") or "annual",
            "reporting_standard": report.get("reporting_standard") or "IFRS",
            "statement_type": rec.get("statement_type") or spec.get("statement_type") or "",
            "line_item_raw": raw,
            "line_item_std": canonical,
            "value_raw": value_raw,
            "value_normalized": value_normalized,
            "currency": rec.get("currency") or "RUB",
            "unit_type": unit_type,
            "unit_multiplier": multiplier,
            "is_calculated": False,
            "formula": None,
            "source_page": rec.get("source_page") if "source_page" in rec else None,
            "source_table_id": rec.get("source_table_id") if "source_table_id" in rec else None,
            "source_url": report.get("source_url"),
            "source_name": "official_ifrs_structured_file",
            "source_type": "official_ifrs",
            "source_priority": 3,
            "is_legacy_data": False,
            "extraction_method": "structured_csv_xlsx",
            "confidence_score": 0.95,
            "quality_score": 90,
            "validation_status": "structured_extracted",
            "created_at": now,
        })
    return rows


def _int_or_none(value) -> int | None:
    try:
        if value is None or pd.isna(value) or str(value).strip() == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--skip-ocr", action="store_true")
    args = parser.parse_args()
    res = extract_financials(Path(args.repo_root), skip_ocr=args.skip_ocr)
    print(f"[extract] reports_extracted={res['reports_extracted']} ocr_skipped={res['ocr_skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
