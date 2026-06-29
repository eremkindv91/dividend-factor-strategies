from __future__ import annotations

import argparse
import re
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
    seed_path = root / "data" / "official_ifrs_facts.csv"
    mapping_path = root / "data" / "mapping" / "ifrs_line_items.yaml"
    if not mapping_path.exists():
        mapping_path = REPO_ROOT / "data" / "mapping" / "ifrs_line_items.yaml"
    mapping = load_ifrs_mapping(mapping_path)
    now = utc_now_iso()
    facts: list[dict] = []
    low_conf: list[dict] = []
    reports_extracted = 0
    reports_failed = 0
    seed_facts_loaded = 0
    reports_skipped_by_seed = 0
    seed_report_ids: set[str] = set()

    if seed_path.exists():
        seeded = read_seed_facts(seed_path)
        seed_facts_loaded = len(seeded)
        seed_report_ids = {str(row.get("report_id")) for row in seeded if row.get("report_id")}
        facts.extend(seeded)

    if report_index.exists():
        reports = pd.read_parquet(report_index)
        for _, report in reports.iterrows():
            if str(report.get("report_id") or "") in seed_report_ids:
                reports_skipped_by_seed += 1
                continue
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
    if not out.empty:
        out = out.drop_duplicates("fact_id", keep="first").reset_index(drop=True)
        out = normalize_ifrs_output(out)
    write_parquet(path, out)
    write_csv(root / "data" / "manual_review" / "extraction_low_confidence.csv", low_conf, LOW_CONF_COLUMNS)
    return {
        "reports_extracted": reports_extracted,
        "reports_failed": reports_failed,
        "reports_skipped_by_seed": reports_skipped_by_seed,
        "facts_from_ifrs": int(len(out)),
        "seed_facts_loaded": int(seed_facts_loaded),
        "ocr_skipped": skip_ocr,
        "mode": "structured_files_only",
    }


def normalize_ifrs_output(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    string_cols = [
        "fact_id", "report_id", "ticker", "inn", "company_name", "period", "period_type",
        "reporting_standard", "statement_type", "line_item_raw", "line_item_std", "value_raw",
        "currency", "unit_type", "formula", "source_page", "source_table_id", "source_url",
        "source_name", "source_type", "extraction_method", "validation_status", "created_at",
    ]
    numeric_cols = [
        "fiscal_year", "value_normalized", "unit_multiplier", "source_priority",
        "confidence_score", "quality_score",
    ]
    bool_cols = ["is_calculated", "is_legacy_data"]
    for col in string_cols:
        if col in out:
            out[col] = out[col].map(lambda v: "" if pd.isna(v) else str(v))
    for col in numeric_cols:
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in bool_cols:
        if col in out:
            out[col] = out[col].map(lambda v: False if pd.isna(v) else bool(v))
    return out


def read_seed_facts(path: Path) -> list[dict]:
    df = pd.read_csv(path)
    rows: list[dict] = []
    for _, rec in df.iterrows():
        row = {col: rec[col] if col in df.columns and not pd.isna(rec[col]) else None for col in IFRS_COLUMNS}
        ticker = str(row.get("ticker") or "").strip().upper()
        canonical = str(row.get("line_item_std") or "").strip()
        if not ticker or not canonical or row.get("value_normalized") is None:
            continue
        if not row.get("fact_id"):
            row["fact_id"] = stable_id("official_seed", ticker, row.get("period"), canonical, row.get("source_url"))
        if not row.get("source_type"):
            row["source_type"] = "official_ifrs"
        if not row.get("source_name"):
            row["source_name"] = "official_ifrs_verified_seed"
        if row.get("source_priority") in (None, ""):
            row["source_priority"] = 3
        if row.get("confidence_score") in (None, ""):
            row["confidence_score"] = 0.9
        if row.get("quality_score") in (None, ""):
            row["quality_score"] = 88
        if not row.get("validation_status"):
            row["validation_status"] = "verified_seed"
        rows.append(row)
    return rows


def extract_structured_report(root: Path, report: dict, file_path: Path, mapping: dict, now: str) -> list[dict]:
    if not file_path.exists():
        raise FileNotFoundError(str(file_path))
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(file_path)
    elif suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(file_path)
    elif suffix in {".html", ".htm"}:
        return extract_html_tables(root, report, file_path, mapping, now)
    elif suffix == ".pdf":
        return extract_pdf_text_tables(root, report, file_path, mapping, now)
    else:
        return []

    required = {"line_item_raw", "value"}
    missing = required - set(df.columns)
    if missing:
        if suffix in {".xlsx", ".xls"}:
            return extract_wide_xlsx(root, report, file_path, mapping, now)
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


def extract_wide_xlsx(root: Path, report: dict, file_path: Path, mapping: dict, now: str) -> list[dict]:
    sheets = pd.read_excel(file_path, sheet_name=None, header=None)
    candidates: dict[str, tuple[int, dict]] = {}
    fiscal_year = _int_or_none(report.get("fiscal_year"))
    for sheet_name, sheet in sheets.items():
        if sheet.empty:
            continue
        multiplier = infer_unit_multiplier(sheet)
        header_idx, value_col = find_year_value_column(sheet, fiscal_year)
        if header_idx is None or value_col is None:
            continue
        for row_idx in range(header_idx + 1, len(sheet)):
            rec = sheet.iloc[row_idx]
            value_raw = rec.iloc[value_col] if value_col < len(rec) else None
            raw_label = row_label_before_value(rec, value_col)
            canonical = map_line_item_loose(raw_label, mapping)
            if not canonical:
                continue
            spec = mapping.get(canonical, {})
            value_normalized = normalize_numeric_value(value_raw, multiplier, spec.get("sign_convention", "as_reported"))
            if value_normalized is None:
                continue
            row = make_fact_row(
                report=report,
                ticker=str(report.get("ticker") or "").strip().upper(),
                fiscal_year=fiscal_year,
                period=str(report.get("period") or fiscal_year or ""),
                raw=raw_label,
                canonical=canonical,
                value_raw=value_raw,
                value_normalized=value_normalized,
                unit_multiplier=multiplier,
                source_name="official_ifrs_xlsx",
                extraction_method="wide_xlsx",
                quality_score=92,
                confidence_score=0.95,
                statement_type=spec.get("statement_type") or "",
                source_table_id=str(sheet_name),
                now=now,
            )
            score = line_item_candidate_score(canonical, raw_label)
            existing = candidates.get(canonical)
            if existing is None or score > existing[0]:
                candidates[canonical] = (score, row)
    return [row for _score, row in candidates.values()]


def line_item_candidate_score(canonical: str, raw_label: str) -> int:
    norm = normalize_label(raw_label)
    canonical_label = canonical.replace("_", " ")
    score = 0
    if norm == canonical_label:
        score += 100
    if norm == f"total {canonical_label}":
        score += 120
    if norm.startswith("total "):
        score += 40
    if canonical == "revenue":
        if norm == "total revenue":
            score += 140
        if any(marker in norm for marker in ("service revenue", "sales of goods", "third parties", "related parties")):
            score -= 80
    if canonical == "total_debt" and any(marker in norm for marker in ("long term", "short term", "current", "lease")):
        score -= 30
    return score


def extract_html_tables(root: Path, report: dict, file_path: Path, mapping: dict, now: str) -> list[dict]:
    try:
        tables = pd.read_html(file_path)
    except ValueError:
        return []
    rows: list[dict] = []
    fiscal_year = _int_or_none(report.get("fiscal_year"))
    for idx, table in enumerate(tables):
        tmp = root / "data" / "manual_review" / f"_html_table_{idx}.xlsx"
        # Reuse the wide-table logic without publishing this temporary file.
        try:
            with pd.ExcelWriter(tmp) as writer:
                table.to_excel(writer, index=False, header=True)
            rows.extend(extract_wide_xlsx(root, {**report, "source_table_id": f"html:{idx}"}, tmp, mapping, now))
        finally:
            if tmp.exists():
                tmp.unlink()
    for row in rows:
        row["source_name"] = "official_ifrs_html"
        row["extraction_method"] = "html_table"
        row["quality_score"] = 88
        row["confidence_score"] = 0.90
        if fiscal_year is not None:
            row["fiscal_year"] = fiscal_year
    return rows


def extract_pdf_text_tables(root: Path, report: dict, file_path: Path, mapping: dict, now: str) -> list[dict]:
    try:
        import pdfplumber
    except Exception:  # noqa: BLE001
        return []
    text_parts = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages[:25]:
                text_parts.append(page.extract_text() or "")
    except Exception:  # noqa: BLE001
        return []
    return extract_text_facts(report, "\n".join(text_parts), mapping, now)


def extract_text_facts(report: dict, text: str, mapping: dict, now: str) -> list[dict]:
    candidates: dict[str, tuple[int, dict]] = {}
    fiscal_year = _int_or_none(report.get("fiscal_year"))
    multiplier = infer_unit_multiplier_from_text(text)
    for line in text.splitlines():
        canonical = map_line_item_loose(line, mapping)
        if not canonical:
            continue
        value_raw = current_period_number_from_line(line)
        if value_raw is None:
            continue
        spec = mapping.get(canonical, {})
        value_normalized = normalize_numeric_value(value_raw, multiplier, spec.get("sign_convention", "as_reported"))
        if value_normalized is None:
            continue
        row = make_fact_row(
            report=report,
            ticker=str(report.get("ticker") or "").strip().upper(),
            fiscal_year=fiscal_year,
            period=str(report.get("period") or fiscal_year or ""),
            raw=line.strip(),
            canonical=canonical,
            value_raw=value_raw,
            value_normalized=value_normalized,
            unit_multiplier=multiplier,
            source_name="official_ifrs_pdf_text",
            extraction_method="pdf_text",
            quality_score=82,
            confidence_score=0.86,
            statement_type=spec.get("statement_type") or "",
            source_table_id=None,
            now=now,
        )
        score = line_item_candidate_score(canonical, line)
        existing = candidates.get(canonical)
        if existing is None or score > existing[0]:
            candidates[canonical] = (score, row)
    return [row for _score, row in candidates.values()]


def current_period_number_from_line(line: str) -> str | None:
    tokens = numeric_tokens(line)
    if not tokens:
        return None
    if looks_like_note_number(tokens):
        tokens = tokens[1:]
    groups = numeric_value_groups(tokens)
    return groups[0] if groups else None


def numeric_tokens(line: str) -> list[str]:
    raw_tokens = str(line).replace("\xa0", " ").replace("\u202f", " ").split()
    tokens: list[str] = []
    for token in raw_tokens:
        cleaned = token.strip().strip(";:")
        cleaned = cleaned.replace("−", "-")
        if re.fullmatch(r"\(?-?\d[\d,.\s]*\)?", cleaned):
            tokens.append(cleaned)
    return tokens


def looks_like_note_number(tokens: list[str]) -> bool:
    if len(tokens) < 3:
        return False
    first = tokens[0].strip("()")
    if not re.fullmatch(r"\d{1,2}", first):
        return False
    second = tokens[1].strip("()")
    if re.fullmatch(r"0\d{2}", second):
        return False
    return len(numeric_value_groups(tokens[1:])) >= 2


def numeric_value_groups(tokens: list[str]) -> list[str]:
    groups: list[str] = []
    i = 0
    while i < len(tokens):
        group = [tokens[i]]
        first_has_separator = numeric_token_has_separator(tokens[i])
        i += 1
        if first_has_separator:
            groups.append(normalize_pdf_number_group(" ".join(group)))
            continue
        while i < len(tokens) and is_numeric_continuation(tokens[i]):
            group.append(tokens[i])
            has_separator = numeric_token_has_separator(tokens[i])
            i += 1
            if has_separator:
                break
        groups.append(normalize_pdf_number_group(" ".join(group)))
    return groups


def normalize_pdf_number_group(value: str) -> str:
    text = value.strip()
    neg_wrap = text.startswith("(") and text.endswith(")")
    core = text[1:-1] if neg_wrap else text
    if " " not in core and "." not in core and re.fullmatch(r"-?\d{1,3}(,\d{3})+", core):
        core = core.replace(",", "")
    return f"({core})" if neg_wrap else core


def numeric_token_has_separator(token: str) -> bool:
    return bool(re.search(r"[,.]", token))


def is_numeric_continuation(token: str) -> bool:
    core = token.strip().strip("()")
    return bool(re.fullmatch(r"\d{3}([,.]\d+)?", core))


def make_fact_row(
    *,
    report: dict,
    ticker: str,
    fiscal_year: int | None,
    period: str,
    raw: str,
    canonical: str,
    value_raw,
    value_normalized: float,
    unit_multiplier: float | int,
    source_name: str,
    extraction_method: str,
    quality_score: int,
    confidence_score: float,
    statement_type: str,
    source_table_id: str | None,
    now: str,
) -> dict:
    fact_id = stable_id("official_ifrs", report.get("report_id"), ticker, period, canonical, raw, source_table_id)
    return {
        "fact_id": fact_id,
        "report_id": report.get("report_id"),
        "ticker": ticker,
        "inn": report.get("inn"),
        "company_name": report.get("company_name"),
        "fiscal_year": fiscal_year,
        "period": period,
        "period_type": report.get("period_type") or "annual",
        "reporting_standard": report.get("reporting_standard") or "IFRS",
        "statement_type": statement_type,
        "line_item_raw": raw,
        "line_item_std": canonical,
        "value_raw": value_raw,
        "value_normalized": value_normalized,
        "currency": "RUB",
        "unit_type": "currency",
        "unit_multiplier": unit_multiplier,
        "is_calculated": False,
        "formula": None,
        "source_page": None,
        "source_table_id": source_table_id,
        "source_url": report.get("source_url"),
        "source_name": source_name,
        "source_type": "official_ifrs",
        "source_priority": 2 if source_name == "official_ifrs_xlsx" else 3,
        "is_legacy_data": False,
        "extraction_method": extraction_method,
        "confidence_score": confidence_score,
        "quality_score": quality_score,
        "validation_status": "structured_extracted",
        "created_at": now,
    }


def find_year_value_column(sheet: pd.DataFrame, fiscal_year: int | None) -> tuple[int | None, int | None]:
    for idx in range(min(len(sheet), 20)):
        values = [str(v).strip() for v in sheet.iloc[idx].tolist()]
        year_cols: list[tuple[int, int]] = []
        for col, value in enumerate(values):
            for year in re.findall(r"(?<!\d)(20[1-3]\d)(?!\d)", value):
                year_cols.append((col, int(year)))
        if not year_cols:
            continue
        if fiscal_year is not None:
            matches = [(col, year) for col, year in year_cols if year == fiscal_year]
            if matches:
                return idx, choose_annual_value_column(sheet, idx, year_cols, matches)
        latest_col, latest_year = sorted(year_cols, key=lambda item: item[1], reverse=True)[0]
        latest_matches = [(col, year) for col, year in year_cols if year == latest_year]
        return idx, choose_annual_value_column(sheet, idx, year_cols, latest_matches)
    return None, None


def choose_annual_value_column(
    sheet: pd.DataFrame,
    header_idx: int,
    year_cols: list[tuple[int, int]],
    matches: list[tuple[int, int]],
) -> int:
    max_col = max(0, sheet.shape[1] - 1)
    year_cols_sorted = sorted(year_cols, key=lambda item: item[0])
    candidates: set[int] = set()
    for col, _year in matches:
        later_year_cols = [next_col for next_col, _ in year_cols_sorted if next_col > col]
        end = min((later_year_cols[0] - 1) if later_year_cols else max_col, max_col)
        candidates.update(range(col, end + 1))
    if not candidates:
        return matches[0][0]
    return max(candidates, key=lambda col: (annual_column_score(sheet, header_idx, col), col))


def annual_column_score(sheet: pd.DataFrame, header_idx: int, col: int) -> int:
    texts: list[str] = []
    for row_idx in range(max(0, header_idx - 1), min(len(sheet), header_idx + 3)):
        value = sheet.iat[row_idx, col] if col < sheet.shape[1] else None
        if not pd.isna(value):
            texts.append(str(value))
    label = normalize_label(" ".join(texts))
    score = 0
    if any(marker in label for marker in ("full year", "fullyear", "12m", "fy", "annual", "year ended", "for the year")):
        score += 100
    if any(marker in label for marker in ("dec 31", "31 dec", "december 31", "31 december")):
        score += 90
    if any(marker in label for marker in ("q4", "4q")):
        score += 30
    if any(marker in label for marker in ("q1", "q2", "q3", "3m", "6m", "9m")):
        score -= 20
    return score


def row_label_before_value(row: pd.Series, value_col: int) -> str:
    candidates = []
    for col in range(min(value_col, len(row))):
        value = row.iloc[col]
        if pd.isna(value):
            continue
        text = str(value).strip()
        if text and not re.fullmatch(r"[-+]?[\d\s,.\u00a0\u202f]+", text):
            candidates.append(text)
    return candidates[-1] if candidates else ""


def infer_unit_multiplier(sheet: pd.DataFrame) -> int:
    parts: list[str] = []
    for _, row in sheet.head(20).iterrows():
        for value in row.tolist():
            if not pd.isna(value):
                parts.append(str(value))
    return infer_unit_multiplier_from_text(" ".join(parts))


def infer_unit_multiplier_from_text(text: str) -> int:
    hay = (text or "").lower()
    if any(marker in hay for marker in ("млрд", "billion", "bn rub", "rub bn")):
        return 1_000_000_000
    if any(marker in hay for marker in ("млн", "million", "rub mln", "mln rub", "ruble million")):
        return 1_000_000
    if any(marker in hay for marker in ("тыс", "thousand")):
        return 1_000
    return 1


def map_line_item_loose(raw: str, mapping: dict) -> str | None:
    exact = map_line_item(raw, mapping)
    if exact:
        return exact
    norm = normalize_label(raw)
    for canonical, spec in mapping.items():
        aliases = [canonical] + spec.get("aliases_ru", []) + spec.get("aliases_en", [])
        for alias in aliases:
            alias_norm = normalize_label(str(alias))
            if alias_norm and (norm == alias_norm or alias_norm in norm):
                return canonical
    return None


def normalize_label(text: str) -> str:
    text = (text or "").lower().replace("\xa0", " ").replace("\u202f", " ")
    text = re.sub(r"[^0-9a-zа-яё]+", " ", text)
    return " ".join(text.split())


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
