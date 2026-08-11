from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any


def parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(text[:10]), time.min)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def asof_date(value: Any) -> str | None:
    parsed = parse_timestamp(value)
    return parsed.date().isoformat() if parsed else None


def age_days(value: Any, now: datetime) -> float | None:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return round((current.astimezone(timezone.utc) - parsed).total_seconds() / 86400, 2)


def point_in_time_quality(publication_timestamp: Any, has_current_data: bool) -> str:
    if parse_timestamp(publication_timestamp) is not None:
        return "verified"
    return "partial" if has_current_data else "unknown"


def get_path(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def latest_timestamp(payload: Any, paths: tuple[str, ...]) -> datetime | None:
    values = [parse_timestamp(get_path(payload, path)) for path in paths]
    candidates = [value for value in values if value is not None]
    return max(candidates) if candidates else None
