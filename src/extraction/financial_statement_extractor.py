from __future__ import annotations

from pathlib import Path


def extract_financial_statement_facts(_file_path: Path) -> list[dict]:
    """Placeholder for later IFRS extraction stage.

    This first increment does not parse PDFs/XLSX automatically; it keeps SmartLab
    baseline reliable and avoids publishing low-confidence OCR/LLM values.
    """
    return []

