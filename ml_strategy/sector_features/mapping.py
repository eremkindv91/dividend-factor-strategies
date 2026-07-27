from __future__ import annotations

from pathlib import Path

from .registry import load_config


def load_sector_mapping(path: Path) -> dict:
    payload = load_config(path)
    packs = payload.get("packs", {})
    seen: dict[str, str] = {}
    for pack, spec in packs.items():
        for ticker in spec.get("priority_tickers", []):
            previous = seen.setdefault(str(ticker), str(pack))
            if previous != pack:
                raise ValueError(f"{ticker}: overlapping priority pack mapping")
    for row in payload.get("issuer_exposures", []):
        weight = float(row.get("weight", 0))
        if not 0 <= weight <= 1:
            raise ValueError("issuer exposure weight must be between zero and one")
        if row.get("available_at") is None:
            raise ValueError("issuer exposure requires available_at")
    return payload


def pack_for_security(ticker: str, sector: str, mapping: dict) -> str | None:
    for pack, spec in mapping.get("packs", {}).items():
        if ticker in spec.get("priority_tickers", []):
            return pack
    candidates = [
        pack
        for pack, spec in mapping.get("packs", {}).items()
        if sector in spec.get("security_master_sectors", [])
    ]
    return candidates[0] if len(candidates) == 1 else None
