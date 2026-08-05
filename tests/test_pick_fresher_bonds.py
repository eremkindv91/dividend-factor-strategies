#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Выбор более свежего среза облигаций при orphan-republish.

Дефект, ради которого скрипт написан: update.yml публиковал закоммиченную копию
site/bonds/, которая отстаёт от того, что bonds_update.yml уже положил в gh-pages.
Каждый деплой откатывал цены назад, safe-фильтр честно отсеивал весь универсум как
устаревший, и раздел выглядел пустым при живых данных на сайте.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pick_fresher_bonds.py"


def _write(path: Path, price_dates):
    path.write_text(json.dumps({
        "bonds": [{"secid": f"B{i}", "source_dates": {"price": d}} for i, d in enumerate(price_dates)]
    }), encoding="utf-8")


def _run(published: Path, checkout: Path) -> int:
    return subprocess.run([sys.executable, str(SCRIPT), str(published), str(checkout)],
                          capture_output=True, text=True).returncode


def test_published_newer_wins(tmp_path):
    pub, chk = tmp_path / "pub.json", tmp_path / "chk.json"
    _write(pub, ["2026-08-05", "2026-08-04"])
    _write(chk, ["2026-07-31", "2026-07-30"])
    assert _run(pub, chk) == 0, "свежий gh-pages должен побеждать отставший чекаут"


def test_checkout_newer_wins(tmp_path):
    pub, chk = tmp_path / "pub.json", tmp_path / "chk.json"
    _write(pub, ["2026-07-31"])
    _write(chk, ["2026-08-05"])
    assert _run(pub, chk) == 1


def test_equal_dates_keep_checkout(tmp_path):
    """При равных датах менять ничего не нужно — лишнее копирование только шумит."""
    pub, chk = tmp_path / "pub.json", tmp_path / "chk.json"
    _write(pub, ["2026-08-05"])
    _write(chk, ["2026-08-05"])
    assert _run(pub, chk) == 1


def test_broken_published_never_replaces_working_checkout(tmp_path):
    """Битый файл на gh-pages не должен подменять рабочие данные."""
    pub, chk = tmp_path / "pub.json", tmp_path / "chk.json"
    pub.write_text("{ это не json", encoding="utf-8")
    _write(chk, ["2026-07-31"])
    assert _run(pub, chk) == 1


def test_missing_dates_are_not_treated_as_fresh(tmp_path):
    pub, chk = tmp_path / "pub.json", tmp_path / "chk.json"
    _write(pub, [None, None])
    _write(chk, ["2026-07-31"])
    assert _run(pub, chk) == 1
