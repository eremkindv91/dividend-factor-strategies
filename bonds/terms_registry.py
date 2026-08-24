"""Verified complex terms registry. Records enrich official market data, never replace it."""
from __future__ import annotations

import json
from pathlib import Path


DEFAULT_TERMS_DIR = Path(__file__).with_name("reference_terms")


def load_verified_terms(secid: str, directory: str | Path = DEFAULT_TERMS_DIR) -> dict:
    path = Path(directory) / f"{secid}.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {"secid", "source", "source_date", "verified_at", "terms_version"}
    missing = sorted(required - payload.keys())
    if missing or payload.get("secid") != secid:
        raise ValueError(f"invalid terms record {path.name}: missing={missing}")
    if not str(payload.get("source", "")).startswith(("https://", "http://")):
        raise ValueError(f"terms source must be a public URL: {path.name}")
    return payload
