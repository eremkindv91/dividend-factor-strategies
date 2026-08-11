#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.ai.artifacts import validate_ai_output_dir  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate publishable AI research artifacts")
    parser.add_argument("--input-dir", type=Path, default=ROOT / "site" / "data" / "research" / "ai")
    parser.add_argument("--require-real", action="store_true")
    parser.add_argument(
        "--require-stocks",
        default="",
        help="Comma-separated stock memo tickers required in status.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors = validate_ai_output_dir(args.input_dir, require_real=args.require_real)
    required = {item.strip().upper() for item in args.require_stocks.split(",") if item.strip()}
    if required:
        try:
            status = json.loads((args.input_dir / "status.json").read_text(encoding="utf-8"))
            present = {str(item).upper() for item in status.get("stock_memos") or []}
            missing = sorted(required - present)
            if missing:
                errors.append(f"required stock memos missing: {', '.join(missing)}")
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            errors.append(f"cannot validate required stock memos: {exc}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"AI research artifacts valid: {args.input_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
