#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ml_strategy.advanced_evaluation import (  # noqa: E402
    build_public_advanced_models,
    run_advanced_evaluation,
)
from ml_strategy.schemas import (  # noqa: E402
    validate_advanced_models,
    validate_public_advanced_models,
    write_json,
)


def main() -> int:
    failure_path = ROOT / "data" / "ml_strategy" / "advanced" / "execution_failure.json"
    try:
        result = run_advanced_evaluation(ROOT, execution_mode="production_evaluation")
        errors = validate_advanced_models(result.payload)
        if errors:
            raise ValueError("; ".join(errors))
        sector_path = ROOT / "site" / "ml_strategy" / "sector_features" / "latest_quality.json"
        sector_quality = (
            json.loads(sector_path.read_text(encoding="utf-8")) if sector_path.exists() else {}
        )
        public_payload = build_public_advanced_models(result.payload, sector_quality)
        public_errors = validate_public_advanced_models(public_payload)
        if public_errors:
            raise ValueError("; ".join(public_errors))
        write_json(ROOT / "data" / "ml_strategy" / "advanced_models.json", result.payload)
        write_json(ROOT / "site" / "ml_strategy" / "advanced_models.json", public_payload)
        if failure_path.exists():
            failure_path.unlink()
    except Exception as exc:
        write_json(
            failure_path,
            {
                "schema_version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "execution_mode": "production_evaluation",
                "status": "EXECUTION_FAILED",
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
            },
        )
        raise
    decisions = {
        row["candidate"]: row["status"]
        for row in result.payload["promotion_decisions"]
    }
    print(
        "[advanced-models] "
        f"common_rows={result.payload['common_test_window']['rows']} "
        f"decisions={json.dumps(decisions, sort_keys=True)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
