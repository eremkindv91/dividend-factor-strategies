from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.paths import REPO_ROOT
from src.pipeline.migrate_existing_smartlab_data import update_registry_from_panel
from src.io_utils import utc_now_iso, write_json


def discover_companies(root: Path = REPO_ROOT) -> dict:
    panel = root / "data" / "panels_final" / "panel_russia_final_smartlab.csv"
    if not panel.exists():
        panel = root / "data" / "panels_final" / "panel_russia_final.csv"
    df = pd.read_csv(panel)
    registry = update_registry_from_panel(root, df, utc_now_iso())
    summary = {"new_companies_added": None, "registry_companies": registry["companies"], "mode": "local_panel_only"}
    write_json(root / "data" / "pipeline_summary.json", {"discover_companies": summary})
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args()
    res = discover_companies(Path(args.repo_root))
    print(f"[discover] registry_companies={res['registry_companies']} mode={res['mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

