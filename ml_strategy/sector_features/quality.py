from __future__ import annotations

from .registry import SourceSpec


def gate_pack_sources(
    required_source_ids: list[str],
    optional_source_ids: list[str],
    registry: dict[str, SourceSpec],
    available_source_ids: set[str],
) -> dict:
    required_missing = [
        source_id
        for source_id in required_source_ids
        if source_id not in available_source_ids
        or source_id not in registry
        or registry[source_id].status != "APPROVED"
    ]
    optional_missing = [
        source_id
        for source_id in optional_source_ids
        if source_id not in available_source_ids
        or source_id not in registry
        or registry[source_id].status != "APPROVED"
    ]
    status = "BLOCKED" if required_missing else ("DEGRADED" if optional_missing else "PASS")
    return {
        "status": status,
        "required_missing": required_missing,
        "optional_missing": optional_missing,
        "preserve_previous_valid_pack": bool(required_missing),
    }
