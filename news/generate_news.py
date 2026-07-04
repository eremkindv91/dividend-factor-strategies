#!/usr/bin/env python3
"""Generate site/news.json from collected news inputs via Gemini JSON mode."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NEWS_DIR = ROOT / "news"
ARTIFACTS = NEWS_DIR / "artifacts"
SITE = ROOT / "site"
MSK = timezone(timedelta(hours=3))
DEFAULT_PROMPT = NEWS_DIR / "prompt.md"
DEFAULT_OUTPUT = SITE / "news.json"
DEFAULT_MODEL = "gemini-3.5-flash"

REQUIRED_TOP = {
    "date": str,
    "generated_at": str,
    "session_open": str,
    "external_backdrop": str,
    "market_snapshot": list,
    "overnight": list,
    "yesterday": list,
    "today_agenda": list,
}
ITEM_CATEGORIES = {"cb_policy", "banks", "markets", "macro", "corporate", "tech", "geopolitics"}
AGENDA_TYPES = {"dividend_cutoff", "earnings", "ofz_auction", "cb_minfin", "macro"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    p.add_argument("--skip-collectors", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="build prompt and validate inputs without Gemini")
    p.add_argument("--model", default=os.getenv("GEMINI_MODEL", DEFAULT_MODEL))
    return p.parse_args()


def run(cmd: list[str]) -> None:
    print("[news] $", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def run_collectors() -> None:
    py = sys.executable
    run([py, "-m", "news.collectors.fetch_news"])
    run([py, "-m", "news.collectors.fetch_markets"])
    run([py, "-m", "news.collectors.load_calendar"])


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def build_prompt(template: str, now: datetime) -> str:
    today = now.date()
    yesterday = today - timedelta(days=1)
    values = {
        "TODAY": today.isoformat(),
        "YESTERDAY": yesterday.isoformat(),
        "NOW_ISO": now.isoformat(),
        "SESSION_OPEN": "10:00 MSK",
        "RU_CLOSE": f"{yesterday.isoformat()} 18:50 MSK",
        "OVERNIGHT_MARKETS": read_text(ARTIFACTS / "overnight_markets.txt").strip(),
        "NEWS_BLOB": read_text(ARTIFACTS / "news_blob.txt").strip(),
        "TODAY_CALENDAR": read_text(ARTIFACTS / "today_calendar.txt").strip(),
    }
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def call_gemini(prompt: str, model_name: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"google-genai is not installed: {e}") from e
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )
    text = getattr(response, "text", "") or ""
    if not text.strip():
        raise RuntimeError("Gemini returned empty response")
    return text


def ensure_iso(value: Any, field: str) -> None:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"{field}: invalid ISO datetime {value!r}") from e


def validate_item(item: Any, idx: int, section: str) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"{section}[{idx}] is not object")
    for key in ("id", "headline", "context", "category", "published_at"):
        if not isinstance(item.get(key), str):
            raise ValueError(f"{section}[{idx}].{key} must be string")
    if item.get("category") not in ITEM_CATEGORIES:
        raise ValueError(f"{section}[{idx}].category invalid: {item.get('category')}")
    if not isinstance(item.get("investment_relevant"), bool):
        raise ValueError(f"{section}[{idx}].investment_relevant must be bool")
    imp = item.get("importance")
    if not isinstance(imp, int) or not (1 <= imp <= 5):
        raise ValueError(f"{section}[{idx}].importance must be int 1..5")
    ensure_iso(item.get("published_at"), f"{section}[{idx}].published_at")
    sources = item.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"{section}[{idx}].sources must be non-empty list")
    for sidx, src in enumerate(sources):
        if not isinstance(src, dict) or not isinstance(src.get("name"), str) or not isinstance(src.get("url"), str):
            raise ValueError(f"{section}[{idx}].sources[{sidx}] invalid")


def validate_news_json(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be object")
    for key, typ in REQUIRED_TOP.items():
        if not isinstance(data.get(key), typ):
            raise ValueError(f"{key} must be {typ.__name__}")
    datetime.fromisoformat(data["date"])
    ensure_iso(data["generated_at"], "generated_at")
    for idx, row in enumerate(data["market_snapshot"]):
        if not isinstance(row, dict):
            raise ValueError(f"market_snapshot[{idx}] is not object")
        for key in ("name", "value", "change_pct", "as_of", "group"):
            if not isinstance(row.get(key), str):
                raise ValueError(f"market_snapshot[{idx}].{key} must be string")
    for section in ("overnight", "yesterday"):
        for idx, item in enumerate(data[section]):
            validate_item(item, idx, section)
    for idx, item in enumerate(data["today_agenda"]):
        if not isinstance(item, dict):
            raise ValueError(f"today_agenda[{idx}] is not object")
        for key in ("time", "event", "ticker", "type"):
            if not isinstance(item.get(key), str):
                raise ValueError(f"today_agenda[{idx}].{key} must be string")
        if item.get("type") not in AGENDA_TYPES:
            raise ValueError(f"today_agenda[{idx}].type invalid: {item.get('type')}")
        imp = item.get("importance")
        if not isinstance(imp, int) or not (1 <= imp <= 5):
            raise ValueError(f"today_agenda[{idx}].importance must be int 1..5")
    return data


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    args = parse_args()
    if not args.skip_collectors:
        run_collectors()
    now = datetime.now(MSK).replace(microsecond=0)
    prompt = build_prompt(args.prompt.read_text(encoding="utf-8"), now)
    (ARTIFACTS / "compiled_prompt.txt").write_text(prompt, encoding="utf-8")
    if args.dry_run:
        print(f"[news] dry-run ok, prompt_chars={len(prompt)}")
        return 0
    try:
        raw = call_gemini(prompt, args.model)
        data = validate_news_json(json.loads(raw))
    except Exception as e:  # noqa: BLE001
        print(f"[news] generation failed; existing {args.output} was not overwritten: {e}", file=sys.stderr)
        return 1
    write_json_atomic(args.output, data)
    print(f"[news] wrote {args.output} model={args.model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
