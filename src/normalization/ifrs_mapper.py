from __future__ import annotations

from pathlib import Path

import yaml


def load_ifrs_mapping(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


def map_line_item(raw: str, mapping: dict) -> str | None:
    raw_norm = (raw or "").strip().lower()
    for canonical, spec in mapping.items():
        aliases = [canonical] + spec.get("aliases_ru", []) + spec.get("aliases_en", [])
        if raw_norm in [str(a).strip().lower() for a in aliases]:
            return canonical
    return None

