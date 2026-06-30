from __future__ import annotations

import json
from pathlib import Path

from src.paths import REPO_ROOT


ALIAS_PATH = Path("src/config/ticker_aliases.json")


def load_ticker_aliases(root: Path = REPO_ROOT) -> dict[str, str]:
    path = root / ALIAS_PATH
    if not path.exists():
        path = REPO_ROOT / ALIAS_PATH
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(k).strip().upper(): str(v).strip().upper() for k, v in raw.items() if str(k).strip() and str(v).strip()}


def canonical_ticker(ticker, root: Path = REPO_ROOT) -> str:
    value = "" if ticker is None else str(ticker).strip().upper()
    if not value:
        return ""
    return load_ticker_aliases(root).get(value, value)


def canonicalize_ticker_series(series, root: Path = REPO_ROOT):
    aliases = load_ticker_aliases(root)
    return series.fillna("").astype(str).str.strip().str.upper().map(lambda value: aliases.get(value, value))
