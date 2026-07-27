from __future__ import annotations

import json

import pytest

from ml_strategy.sector_features.mapping import load_sector_mapping, pack_for_security


def test_unknown_security_does_not_receive_random_pack(tmp_path):
    path = tmp_path / "mapping.yml"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "packs": {
                    "BANKS": {
                        "security_master_sectors": ["Banks"],
                        "priority_tickers": ["SBER"],
                    }
                },
                "issuer_exposures": [],
            }
        ),
        encoding="utf-8",
    )
    mapping = load_sector_mapping(path)
    assert pack_for_security("UNKNOWN", "Other", mapping) is None
    assert pack_for_security("SBER", "Other", mapping) == "BANKS"


def test_exposure_requires_valid_weight_and_available_at(tmp_path):
    path = tmp_path / "mapping.yml"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "packs": {},
                "issuer_exposures": [{"ticker": "SBER", "weight": 1.2}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="weight"):
        load_sector_mapping(path)
