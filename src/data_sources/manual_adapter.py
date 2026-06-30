from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.io_utils import stable_id, utc_now_iso


def read_manual_override(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    required = {
        "ticker",
        "fiscal_year",
        "period",
        "reporting_standard",
        "statement_type",
        "line_item_std",
        "value_normalized",
        "source_url",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path}: required manual override columns missing: {sorted(missing)}")

    now = utc_now_iso()
    out = df.copy()
    if "ready_for_manual_override" in out:
        ready = out["ready_for_manual_override"].apply(_truthy)
        out = out[ready].copy()
        if out.empty:
            return pd.DataFrame()
    out["ticker"] = out["ticker"].astype(str).str.strip().str.upper()
    out["period"] = out["period"].astype(str).str.strip()
    out["fact_id"] = out.apply(
        lambda r: stable_id(
            "manual_override",
            r.get("ticker"),
            r.get("fiscal_year"),
            r.get("period"),
            r.get("reporting_standard"),
            r.get("statement_type"),
            r.get("line_item_std"),
        ),
        axis=1,
    )
    out["source_name"] = "manual_override"
    out["source_type"] = "manual_override"
    out["source_priority"] = 1
    out["is_legacy_data"] = False
    out["extraction_method"] = "manual_override"
    out["confidence_score"] = 1.0
    out["quality_score"] = 100
    out["validation_status"] = "manual_override"
    if "created_at" not in out:
        out["created_at"] = now
    return out


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}
