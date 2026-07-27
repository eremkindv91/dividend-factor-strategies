from __future__ import annotations

import json

import pytest

from ml_strategy.sector_features.registry import load_source_registry
from ml_strategy.sector_features.quality import gate_pack_sources


def test_approved_source_requires_real_provenance(tmp_path):
    path = tmp_path / "sources.yml"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "registry_version": "test",
                "sources": [
                    {
                        "series_id": "BAD",
                        "label": "bad",
                        "provider": None,
                        "source_url": None,
                        "frequency": "daily",
                        "availability_lag_calendar_days": 0,
                        "revision_policy": "append_only",
                        "required_by": [],
                        "status": "APPROVED",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="provenance"):
        load_source_registry(path)


def test_required_failure_blocks_pack_and_preserves_previous_valid(tmp_path):
    path = tmp_path / "sources.yml"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "registry_version": "test",
                "sources": [
                    {
                        "series_id": "OFFICIAL",
                        "label": "official",
                        "provider": "Exchange",
                        "source_url": "https://example.test/official",
                        "frequency": "daily",
                        "availability_lag_calendar_days": 0,
                        "revision_policy": "append_only",
                        "required_by": ["PACK"],
                        "status": "APPROVED",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _, registry = load_source_registry(path)
    result = gate_pack_sources(["OFFICIAL"], [], registry, set())
    assert result["status"] == "BLOCKED"
    assert result["preserve_previous_valid_pack"] is True
