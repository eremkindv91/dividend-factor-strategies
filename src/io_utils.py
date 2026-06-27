from __future__ import annotations

import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from src.paths import ensure_dir


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
      for chunk in iter(lambda: f.read(1024 * 1024), b""):
        h.update(chunk)
    return h.hexdigest()


def stable_id(*parts: object) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def write_json(path: Path, obj: object) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: Iterable[dict], fieldnames: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k) for k in fieldnames})


def write_empty_csv(path: Path, fieldnames: list[str]) -> None:
    write_csv(path, [], fieldnames)


def write_parquet(path: Path, df: pd.DataFrame) -> None:
    ensure_dir(path.parent)
    df.to_parquet(path, index=False)


def backup_files(files: list[Path], backup_root: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ensure_dir(backup_root / ts)
    manifest = []
    for src in files:
        if not src.exists() or not src.is_file():
            continue
        dst = out / src.name
        shutil.copy2(src, dst)
        manifest.append({
            "source_path": str(src),
            "backup_path": str(dst),
            "sha256": sha256_file(src),
            "size": src.stat().st_size,
        })
    write_json(out / "manifest.json", {"created_at": utc_now_iso(), "files": manifest})
    return out

