#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.fingerprints import aggregate_fingerprint, fingerprint  # noqa: E402
from src.research.schemas import RESEARCH_ARTIFACTS  # noqa: E402
from src.research.validators import validate_research_bundle  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate public-safe deterministic research artifacts")
    parser.add_argument("--input-dir", type=Path, default=ROOT / "site" / "data" / "research")
    return parser.parse_args(argv)


def validate_directory(path: Path) -> list[str]:
    errors: list[str] = []
    artifacts: dict[str, dict] = {}
    required = (*RESEARCH_ARTIFACTS, "research_manifest.json")
    for name in required:
        artifact = path / name
        if not artifact.exists():
            errors.append(f"missing artifact: {name}")
            continue
        try:
            artifacts[name] = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid JSON {name}: {exc}")
    if errors:
        return errors

    stock_index = artifacts["stock_index.json"]
    for row in stock_index.get("stocks", []):
        relative = str(row.get("path") or "").removeprefix("data/research/")
        if not relative.startswith("stocks/"):
            errors.append(f"stock_index.json: invalid stock path for {row.get('ticker')}")
            continue
        path_item = path / relative
        try:
            artifacts[relative] = json.loads(path_item.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid stock artifact {relative}: {exc}")
    if errors:
        return errors

    result = validate_research_bundle(artifacts)
    errors.extend(result.errors)
    component_files = {
        "market": "market_snapshot.json",
        "fundamentals": "fundamentals_snapshot.json",
        "sectors": "sector_snapshot.json",
        "stocks": "stock_index.json",
        "ml": "ml_snapshot.json",
        "banks": "bank_snapshot.json",
        "bonds": "bond_snapshot.json",
        "news": "news_snapshot.json",
    }
    manifest = artifacts["research_manifest.json"]
    actual_hashes: dict[str, str] = {}
    for component, name in component_files.items():
        payload = dict(artifacts[name])
        claimed = payload.pop("fingerprint", None)
        actual = fingerprint(payload)
        actual_hashes[component] = actual
        if claimed != actual:
            errors.append(f"{name}: fingerprint mismatch")
        manifest_claim = (manifest.get("components", {}).get(component) or {}).get("fingerprint")
        if manifest_claim != actual:
            errors.append(f"research_manifest.json: fingerprint mismatch for {component}")
    if manifest.get("research_input_hash") != aggregate_fingerprint(actual_hashes):
        errors.append("research_manifest.json: aggregate research_input_hash mismatch")
    stock_hashes: dict[str, str] = {}
    for row in artifacts["stock_index.json"].get("stocks", []):
        ticker = str(row.get("ticker") or "")
        relative = str(row.get("path") or "").removeprefix("data/research/")
        payload = dict(artifacts.get(relative) or {})
        claimed = payload.pop("fingerprint", None)
        actual = fingerprint(payload)
        stock_hashes[ticker] = actual
        if claimed != actual or row.get("fingerprint") != actual:
            errors.append(f"{relative}: stock fingerprint mismatch")
    if artifacts["stock_index.json"].get("stock_payload_hash") != fingerprint(stock_hashes):
        errors.append("stock_index.json: stock_payload_hash mismatch")
    if manifest.get("ready_for_ai") and manifest.get("validation_errors"):
        errors.append("research_manifest.json: ready_for_ai=true with validation_errors")
    return sorted(set(errors))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors = validate_directory(args.input_dir)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"research artifacts valid: {args.input_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
