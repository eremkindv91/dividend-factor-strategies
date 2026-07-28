#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ml_strategy.schemas import (  # noqa: E402
    validate_advanced_models,
    validate_public_advanced_models,
)


def main() -> int:
    paths = (
        ROOT / "data" / "ml_strategy" / "advanced_models.json",
        ROOT / "site" / "ml_strategy" / "advanced_models.json",
    )
    payloads = []
    validators = (validate_advanced_models, validate_public_advanced_models)
    for path, validator in zip(paths, validators):
        if not path.exists():
            print(f"[advanced-models] missing {path.relative_to(ROOT)}", file=sys.stderr)
            return 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[advanced-models] invalid {path.relative_to(ROOT)}: {exc}", file=sys.stderr)
            return 1
        errors = validator(payload)
        if errors:
            print("\n".join(f"[advanced-models] {error}" for error in errors), file=sys.stderr)
            return 1
        payloads.append(payload)
    if payloads[0]["generated_at"] != payloads[1]["generated_at"]:
        print("[advanced-models] private/public generated_at differs", file=sys.stderr)
        return 1
    print(
        "[advanced-models] valid "
        f"run_id={payloads[0].get('run_id')} "
        f"common_rows={payloads[1]['evaluation_window']['oos_rows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
