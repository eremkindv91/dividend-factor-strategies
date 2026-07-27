#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ml_strategy.schemas import validate_bundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", default=str(ROOT / "site" / "ml_strategy"))
    args = parser.parse_args()
    errors = validate_bundle(args.directory)
    if errors:
        for error in errors:
            print(f"[ml-strategy] ERROR: {error}", file=sys.stderr)
        return 1
    print(f"[ml-strategy] valid snapshot bundle: {args.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
