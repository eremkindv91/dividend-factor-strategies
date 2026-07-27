from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceSpec:
    series_id: str
    label: str
    provider: str | None
    source_url: str | None
    frequency: str
    availability_lag_calendar_days: int
    revision_policy: str
    required_by: tuple[str, ...]
    status: str
    reason: str | None = None


def load_config(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError(f"{path.name}: unsupported schema_version")
    return payload


def load_source_registry(path: Path) -> tuple[str, dict[str, SourceSpec]]:
    payload = load_config(path)
    specs: dict[str, SourceSpec] = {}
    for row in payload.get("sources", []):
        spec = SourceSpec(
            series_id=str(row["series_id"]),
            label=str(row["label"]),
            provider=row.get("provider"),
            source_url=row.get("source_url"),
            frequency=str(row["frequency"]),
            availability_lag_calendar_days=int(row.get("availability_lag_calendar_days", 0)),
            revision_policy=str(row["revision_policy"]),
            required_by=tuple(row.get("required_by", [])),
            status=str(row["status"]),
            reason=row.get("reason"),
        )
        if spec.series_id in specs:
            raise ValueError(f"duplicate series_id: {spec.series_id}")
        if spec.status == "APPROVED" and (not spec.provider or not spec.source_url):
            raise ValueError(f"{spec.series_id}: approved source lacks provenance")
        specs[spec.series_id] = spec
    return str(payload["registry_version"]), specs
