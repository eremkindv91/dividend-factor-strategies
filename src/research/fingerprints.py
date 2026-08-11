from __future__ import annotations

import hashlib
import json
import math
from typing import Any

# Build/check timestamps do not alter the economic research input. Source as-of dates,
# model statuses and values remain in the hash.
VOLATILE_KEYS = {
    "age_days",
    "calculated_at",
    "checked_at",
    "fetched_at",
    "fresh",
    "generated_at",
    "ingested_at",
    "last_checked_at",
    "rating_checked_at",
    "run_id",
    "updated_at",
}


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _stable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key).lower() not in VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite number cannot be fingerprinted")
    return value


def stable_json(value: Any) -> str:
    return json.dumps(
        _stable(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def aggregate_fingerprint(components: dict[str, str]) -> str:
    return fingerprint({"components": dict(sorted(components.items()))})
