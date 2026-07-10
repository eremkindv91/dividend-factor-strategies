#!/usr/bin/env python3
"""Keep the newest valid news.json during a full-site deployment.

The full update workflow starts from the source branch, while the dedicated
news workflow may already have published a newer file to gh-pages.  This gate
prevents a later full deployment from replacing that newer digest with an
older source copy.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def load_valid(path: Path) -> tuple[dict, datetime] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        generated = datetime.fromisoformat(str(data["generated_at"]).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        else:
            generated = generated.astimezone(timezone.utc)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(data.get("market_snapshot"), list) or not data["market_snapshot"]:
        return None
    for key in ("overnight", "yesterday", "today_agenda"):
        if not isinstance(data.get(key), list):
            return None
    return data, generated


def preserve_freshest(candidate: Path, published: Path | None) -> str:
    local = load_valid(candidate)
    remote = load_valid(published) if published else None
    if local is None and remote is None:
        raise ValueError("no valid candidate or published news.json")
    if remote is not None and (local is None or remote[1] > local[1]):
        candidate.parent.mkdir(parents=True, exist_ok=True)
        tmp = candidate.with_suffix(candidate.suffix + ".tmp")
        tmp.write_text(
            json.dumps(remote[0], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp, candidate)
        return "published"
    return "candidate"


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print("usage: preserve_freshest_news.py CANDIDATE [PUBLISHED]", file=sys.stderr)
        return 2
    candidate = Path(sys.argv[1])
    published = Path(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[2] else None
    try:
        selected = preserve_freshest(candidate, published)
    except ValueError as exc:
        print(f"[news-gate] {exc}", file=sys.stderr)
        return 1
    print(f"[news-gate] selected {selected} news.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
