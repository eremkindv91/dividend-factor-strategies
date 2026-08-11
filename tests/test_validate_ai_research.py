from __future__ import annotations

import json

from scripts.validate_ai_research import main


def test_required_stock_memos_make_validation_fail(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("scripts.validate_ai_research.validate_ai_output_dir", lambda *_args, **_kwargs: [])
    (tmp_path / "status.json").write_text(
        json.dumps({"stock_memos": ["SBER"]}),
        encoding="utf-8",
    )
    result = main(
        ["--input-dir", str(tmp_path), "--require-stocks", "SBER,GAZP,YDEX"]
    )
    assert result == 1
    assert "required stock memos missing: GAZP, YDEX" in capsys.readouterr().out


def test_required_stock_memos_pass_when_all_are_present(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.validate_ai_research.validate_ai_output_dir", lambda *_args, **_kwargs: [])
    (tmp_path / "status.json").write_text(
        json.dumps({"stock_memos": ["SBER", "GAZP", "YDEX"]}),
        encoding="utf-8",
    )
    assert main(
        ["--input-dir", str(tmp_path), "--require-stocks", "SBER,GAZP,YDEX"]
    ) == 0
