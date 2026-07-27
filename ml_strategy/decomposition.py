from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


class IceemdanUnavailable(RuntimeError):
    """Raised when no audited ICEEMDAN implementation is configured."""


def expanding_iceemdan_features(
    series: pd.Series,
    prediction_dates: list[pd.Timestamp],
    backend: Callable[[np.ndarray, int], dict[str, float]] | None,
    cache_dir: Path | None = None,
    min_history: int = 252,
    seed: int = 42,
) -> pd.DataFrame:
    """Run an injected ICEEMDAN backend on expanding histories only.

    The project intentionally does not alias CEEMDAN or EEMD to ICEEMDAN. A
    backend must be explicitly supplied and audited before these features can
    influence production.
    """
    if backend is None:
        raise IceemdanUnavailable("audited ICEEMDAN backend is not configured")
    clean = series.dropna().sort_index()
    rows: list[dict] = []
    for prediction_date in sorted(pd.Timestamp(value) for value in prediction_dates):
        history = clean.loc[:prediction_date]
        if len(history) < min_history:
            continue
        digest = hashlib.sha256(
            history.index.astype(str).str.cat(sep="|").encode("utf-8")
            + history.to_numpy(dtype=float).tobytes()
            + str(seed).encode("ascii")
        ).hexdigest()
        cache_path = cache_dir / f"{digest}.json" if cache_dir else None
        if cache_path and cache_path.exists():
            features = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            features = backend(history.to_numpy(dtype=float), seed)
            if not isinstance(features, dict) or not features:
                raise ValueError("ICEEMDAN backend returned no feature mapping")
            if not all(isinstance(value, (int, float)) and np.isfinite(value) for value in features.values()):
                raise ValueError("ICEEMDAN backend returned non-finite features")
            if cache_path:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(features, sort_keys=True) + "\n", encoding="utf-8")
        rows.append({"date": prediction_date, **features})
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()
