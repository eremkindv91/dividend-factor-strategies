#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Детерминированная фикстура для parity-теста Ledoit–Wolf (JS ↔ sklearn).

Зачем: ковариация для границы эффективности считается В БРАУЗЕРЕ (портфель пользователя
не должен покидать устройство — инвариант проекта), значит Ledoit–Wolf пришлось написать
на JS. Формулу нельзя переносить «на глаз»: shrinkage intensity выводится из четвёртых
моментов, и правдоподобно выглядящая опечатка даёт правдоподобно выглядящую матрицу.

Поэтому эталон берём у эталонной реализации (sklearn.covariance.LedoitWolf) на
фиксированных данных и держим его в репозитории как контракт. JS-тест сверяется с ним.

Запуск:  python scripts/build_frontier_fixture.py
Выход:   tests/fixtures/ledoit_wolf.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.covariance import LedoitWolf, ledoit_wolf_shrinkage

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "ledoit_wolf.json"

# Фиксированный seed: фикстура должна быть воспроизводимой байт-в-байт.
SEED = 20260729


def make_case(name: str, n_obs: int, n_assets: int, rng: np.random.Generator,
              corr: float = 0.35, vol_lo: float = 0.03, vol_hi: float = 0.14) -> dict:
    """Ряды с реалистичной для акций структурой: общий фактор + идиосинкразия."""
    vols = rng.uniform(vol_lo, vol_hi, n_assets)
    common = rng.standard_normal((n_obs, 1))
    idio = rng.standard_normal((n_obs, n_assets))
    x = (np.sqrt(corr) * common + np.sqrt(1.0 - corr) * idio) * vols
    lw = LedoitWolf().fit(x)
    return {
        "name": name,
        "n_obs": n_obs,
        "n_assets": n_assets,
        # returns[t][i] — месячные доходности, ровно тот layout, что приходит из returns.json
        "returns": [[round(float(v), 10) for v in row] for row in x],
        "expected_shrinkage": float(ledoit_wolf_shrinkage(x)),
        "expected_covariance": [[round(float(v), 12) for v in row] for row in lw.covariance_],
    }


def main() -> int:
    rng = np.random.default_rng(SEED)
    cases = [
        # типичный портфель частного инвестора: активов сильно меньше наблюдений
        make_case("typical_10x91", 91, 10, rng),
        # мало истории: shrinkage обязан быть заметно сильнее
        make_case("short_history_12x40", 40, 12, rng),
        # активов больше, чем наблюдений: выборочная ковариация вырождена, LW спасает
        make_case("singular_40x24", 24, 40, rng),
        # почти некоррелированные бумаги
        make_case("low_corr_8x91", 91, 8, rng, corr=0.02),
        # сильно скоррелированные (один сектор)
        make_case("high_corr_8x91", 91, 8, rng, corr=0.85),
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generator": "scripts/build_frontier_fixture.py",
        "seed": SEED,
        "reference": "sklearn.covariance.LedoitWolf (identity-scaled target)",
        "note": "Эталон для parity-теста JS-реализации. Не редактировать руками — перегенерировать скриптом.",
        "cases": cases,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    for case in cases:
        print(f"[fixture] {case['name']:22} shrinkage={case['expected_shrinkage']:.6f}")
    print(f"[fixture] записано: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
