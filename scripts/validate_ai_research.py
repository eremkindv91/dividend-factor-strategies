#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors = validate_ai_output_dir(args.input_dir, require_real=args.require_real)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"AI research artifacts valid: {args.input_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
