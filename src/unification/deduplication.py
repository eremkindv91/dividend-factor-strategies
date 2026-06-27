from __future__ import annotations

import pandas as pd


FACT_DEDUP_FIELDS = [
    "ticker",
    "fiscal_year",
    "period",
    "reporting_standard",
    "statement_type",
    "line_item_std",
    "currency",
]


def fact_dedup_key(row: dict | pd.Series) -> str:
    return "|".join(str(row.get(k, "") or "") for k in FACT_DEDUP_FIELDS)


def add_fact_dedup_key(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["dedup_key"] = out.apply(fact_dedup_key, axis=1)
    return out


def deduplicate_facts(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if df.empty:
        return df.copy(), 0
    work = add_fact_dedup_key(df)
    for col, default in (("quality_score", 0), ("source_priority", 99), ("confidence_score", 0)):
        if col not in work:
            work[col] = default
    work = work.sort_values(
        ["dedup_key", "quality_score", "confidence_score", "source_priority"],
        ascending=[True, False, False, True],
    )
    out = work.drop_duplicates("dedup_key", keep="first").drop(columns=["dedup_key"])
    return out.reset_index(drop=True), len(work) - len(out)

