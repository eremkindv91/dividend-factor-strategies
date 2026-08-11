from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from .freshness import parse_timestamp
from .schemas import (
    COMPONENT_STATUSES,
    POINT_IN_TIME_QUALITIES,
    REQUIRED_MANIFEST_COMPONENTS,
    RESEARCH_SCHEMA_VERSION,
)

SECRET_VALUE_PATTERNS = (
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{12,}", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
SECRET_KEY_PATTERN = re.compile(r"(?:^|_)(?:api_?key|auth(?:orization)?|password|secret|token)(?:$|_)", re.I)
LOCAL_PATH_PATTERNS = (
    re.compile(r"^/"),
    re.compile(r"^[A-Za-z]:[\\/]"),
)
PRIVATE_KEYS = {
    "average_purchase_price",
    "cost_basis",
    "holdings",
    "localstorage",
    "localstorage_dump",
    "pnl",
    "portfolio_weights",
    "portfolio_holdings",
    "portfolio_quantities",
    "profit_loss",
    "purchase_price",
    "quantities",
    "quantity",
    "user_portfolio",
    "user_settings",
    "user_weight",
    "user_weights",
}
TIMESTAMP_KEYS = {
    "as_of",
    "asof",
    "available_at",
    "calculated_at",
    "checked_at",
    "data_as_of",
    "data_last",
    "forecast_asof",
    "generated_at",
    "ingestion_date",
    "last_checked_at",
    "price_asof",
    "publication_date",
    "published_at",
    "rating_checked_at",
    "report_period_end",
    "research_asof",
    "source_as_of",
}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: "ValidationResult") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


def walk(value: Any, path: str = "$") -> Iterator[tuple[str, str | None, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            yield child, str(key), item
            yield from walk(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{path}[{index}]"
            yield child, None, item
            yield from walk(item, child)


def _schema_errors(name: str, payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"{name}: top-level value must be an object"]
    if payload.get("schema_version") != RESEARCH_SCHEMA_VERSION:
        errors.append(f"{name}: unsupported schema_version")
    if name == "research_manifest.json":
        missing = sorted(REQUIRED_MANIFEST_COMPONENTS - set(payload.get("components", {})))
        if missing:
            errors.append(f"{name}: missing components: {', '.join(missing)}")
        if not str(payload.get("research_input_hash", "")).startswith("sha256:"):
            errors.append(f"{name}: research_input_hash is missing")
        for component, row in payload.get("components", {}).items():
            if not isinstance(row, dict) or row.get("status") not in COMPONENT_STATUSES:
                errors.append(f"{name}: invalid status for component {component}")
            if not str((row or {}).get("fingerprint", "")).startswith("sha256:"):
                errors.append(f"{name}: missing fingerprint for component {component}")
    if name == "sector_snapshot.json":
        sectors = payload.get("sectors")
        if not isinstance(sectors, list):
            errors.append(f"{name}: sectors must be a list")
        else:
            for row in sectors:
                model = row.get("model", {}) if isinstance(row, dict) else {}
                status = model.get("promotion_status")
                if status != "APPROVED" and model.get("tradable_signal") is not False:
                    errors.append(
                        f"{name}: {row.get('sector')} has {status or 'no status'} but tradable_signal is not false"
                    )
    return errors


def validate_public_artifact(
    name: str,
    payload: Any,
    *,
    now: datetime | None = None,
) -> ValidationResult:
    result = ValidationResult(errors=_schema_errors(name, payload))
    result.extend(validate_safe_content(name, payload, now=now))
    return result


def validate_safe_content(
    name: str,
    payload: Any,
    *,
    now: datetime | None = None,
) -> ValidationResult:
    result = ValidationResult()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    future_limit = current.astimezone(timezone.utc) + timedelta(minutes=5)

    for path, key, value in walk(payload):
        if isinstance(value, float) and not math.isfinite(value):
            result.errors.append(f"{name}: non-finite number at {path}")
        if key and key.lower() in PRIVATE_KEYS:
            result.errors.append(f"{name}: private portfolio field at {path}")
        if key and SECRET_KEY_PATTERN.search(key) and value not in (None, "", False):
            result.errors.append(f"{name}: secret-like field at {path}")
        if isinstance(value, str):
            if any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS):
                result.errors.append(f"{name}: secret-like value at {path}")
            if any(pattern.search(value) for pattern in LOCAL_PATH_PATTERNS):
                result.errors.append(f"{name}: local absolute path at {path}")
            if key and key.lower() in TIMESTAMP_KEYS:
                parsed = parse_timestamp(value)
                if parsed and parsed > future_limit:
                    result.errors.append(f"{name}: future timestamp at {path}: {value}")
        if key == "point_in_time_quality" and value not in POINT_IN_TIME_QUALITIES:
            result.errors.append(f"{name}: invalid point_in_time_quality at {path}")
    return result


def validate_research_bundle(
    artifacts: dict[str, dict],
    *,
    now: datetime | None = None,
) -> ValidationResult:
    result = ValidationResult()
    for name, payload in artifacts.items():
        result.extend(validate_public_artifact(name, payload, now=now))
    return result
