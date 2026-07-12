#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily Market Data Foundation — Фаза 2: хранилище дневных рядов (parquet + манифест).

Детерминированное, атомарное, с checksum по ДАННЫМ (не по байтам parquet — те содержат
недетерминированные метаданные). Чистый stdlib + pandas/pyarrow. Ряды большие → gitignore,
генерятся в CI (как site/data.json). Публичный слой — компактный, отдельно (Фаза 4).
"""
from __future__ import annotations

import hashlib
import json
import os

ROW_COLUMNS = ["trade_date", "secid", "open", "high", "low", "close", "value", "volume"]
SCHEMA_VERSION = 1


def canonical_rows(rows: list[dict]) -> list[dict]:
    """Отсортированные по дате, дедуп (последняя запись даты побеждает), только контрактные поля."""
    by_date = {}
    for r in rows:
        d = str(r.get("trade_date"))[:10]
        if len(d) != 10:
            continue
        by_date[d] = {k: r.get(k) for k in ROW_COLUMNS}
        by_date[d]["trade_date"] = d
    return [by_date[d] for d in sorted(by_date)]


def checksum(rows: list[dict]) -> str:
    """sha256 по канонической форме данных (стабилен между запусками, не зависит от parquet-байтов)."""
    canon = canonical_rows(rows)
    payload = json.dumps(canon, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_parquet(path: str, rows: list[dict]) -> int:
    """Атомарная запись отсортированных рядов в parquet. Возвращает число строк."""
    import pandas as pd
    canon = canonical_rows(rows)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df = pd.DataFrame(canon, columns=ROW_COLUMNS)
    tmp = path + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return len(canon)


def read_parquet(path: str) -> list[dict]:
    """Прочитать ряд в list[dict]; отсутствует/битый → []."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    try:
        import pandas as pd
        df = pd.read_parquet(path)
        return canonical_rows(df.to_dict(orient="records"))
    except Exception:  # noqa: BLE001
        return []


def load_manifest(path: str) -> dict:
    if not os.path.exists(path):
        return {"schema_version": SCHEMA_VERSION, "series": {}}
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        m.setdefault("series", {})
        return m
    except (OSError, ValueError):
        return {"schema_version": SCHEMA_VERSION, "series": {}}


def write_manifest(path: str, manifest: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)
