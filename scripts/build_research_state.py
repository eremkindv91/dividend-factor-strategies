#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.state_builder import build_research_state  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build deterministic research snapshots from existing site artifacts")
    parser.add_argument("--site-dir", type=Path, default=ROOT / "site")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "site" / "data" / "research")
    parser.add_argument(
        "--fallback-root",
        action="append",
        type=Path,
        default=[],
        help="Optional gh-pages/last-good root; the freshest source artifact is selected per file",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    artifacts = build_research_state(
        ROOT,
        site_dir=args.site_dir,
        output_dir=args.output_dir,
        fallback_roots=args.fallback_root,
    )
    manifest = artifacts["research_manifest.json"]
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "artifacts": len(artifacts),
                "ready_for_ai": manifest["ready_for_ai"],
                "research_asof": manifest["research_asof"],
                "research_input_hash": manifest["research_input_hash"],
                "validation_errors": manifest["validation_errors"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
