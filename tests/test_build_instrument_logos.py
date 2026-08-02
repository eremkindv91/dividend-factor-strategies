from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from scripts import build_instrument_logos as logos


def png(width: int = 160, height: int = 160) -> bytes:
    # The builder validates signature/IHDR/dimensions, not pixel decoding.
    return logos.PNG_SIGNATURE + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"


class Response:
    def __init__(self, payload=None, content=b"", status=200):
        self.payload = payload
        self.content = content
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise logos.requests.HTTPError(f"http_{self.status}")

    def json(self):
        return self.payload


class ApiSession:
    def __init__(self, shares, funds=()):
        self.shares = list(shares)
        self.funds = list(funds)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        rows = self.funds if url.endswith("/Etfs") else self.shares
        return Response({"instruments": rows})


def instrument(ticker: str, logo_name: str, *, board="TQBR", name="Компания"):
    return {
        "ticker": ticker,
        "name": name,
        "classCode": board,
        "currency": "rub",
        "countryOfRisk": "RU",
        "brand": {"logoName": logo_name},
    }


def write_universe(path: Path):
    path.write_text(
        json.dumps({"tickers": [
            {"ticker": "SBER", "name": "Сбербанк"},
            {"ticker": "SBERP", "name": "Сбербанк-п"},
            {"ticker": "EQMX", "name": "Фонд"},
            {"ticker": "BAD", "name": "Ошибка"},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_builds_local_registry_without_exposing_token(tmp_path, monkeypatch):
    universe = tmp_path / "data.json"
    output = tmp_path / "out"
    write_universe(universe)
    api = ApiSession(
        [instrument("SBER", "sber.png"), instrument("SBER", "wrong.png", board="SPBXM"),
         instrument("SBERP", "sber.png"), instrument("BAD", "bad.png")],
        [instrument("EQMX", "eqmx.png")],
    )
    cdn_calls = []

    def fake_get(url, **kwargs):
        cdn_calls.append((url, kwargs))
        return Response(content=b"not an image" if "badx160" in url else png())

    monkeypatch.setattr(logos.requests, "get", fake_get)
    secret = "secret-token-must-never-be-written"
    summary = logos.build(universe, output, secret, session=api)

    assert summary == {
        "status": "ok", "universe": 4, "catalogue_matches": 4,
        "downloaded": 3, "registry_count": 3, "failed": 1,
    }
    registry_text = (output / "instrument_logos.js").read_text(encoding="utf-8")
    manifest = json.loads((output / "assets/instruments/company_manifest.json").read_text(encoding="utf-8"))
    assert secret not in registry_text
    assert secret not in json.dumps(manifest)
    assert "assets/instruments/companies/sber.png" in registry_text
    assert '"type":"fund"' in registry_text
    assert not (output / "assets/instruments/companies/bad.png").exists()
    assert all(call[1]["headers"]["Authorization"] == f"Bearer {secret}" for call in api.calls)
    assert all("headers" not in kwargs for _, kwargs in cdn_calls), "CDN must not receive API authorization"


def test_disabled_or_invalid_source_never_writes_broken_registry(tmp_path, monkeypatch):
    universe = tmp_path / "data.json"
    output = tmp_path / "out"
    write_universe(universe)
    assert logos.build(universe, output, "")["status"] == "disabled"
    assert not output.exists()

    api = ApiSession([instrument("SBER", "../../escape.png")])
    monkeypatch.setattr(logos.requests, "get", lambda *args, **kwargs: Response(content=png()))
    with pytest.raises(logos.LogoBuildError, match="no valid logos"):
        logos.build(universe, output, "token", session=api)
    assert not (output / "instrument_logos.js").exists()


def test_previous_valid_registry_survives_partial_refresh(tmp_path, monkeypatch):
    universe = tmp_path / "data.json"
    output = tmp_path / "out"
    previous = tmp_path / "previous.js"
    write_universe(universe)
    previous.write_text(
        'window.InstrumentLogoRegistry=Object.freeze({"SBER":{"logo_path":"assets/instruments/companies/sber.png","name":"Сбербанк","type":"equity"}});\n',
        encoding="utf-8",
    )
    api = ApiSession([instrument("BAD", "bad.png")])
    monkeypatch.setattr(logos.requests, "get", lambda *args, **kwargs: Response(content=b"html error"))
    summary = logos.build(universe, output, "token", session=api, previous_registry=previous)
    assert summary["downloaded"] == 0
    assert summary["registry_count"] == 1
    assert "SBER" in (output / "instrument_logos.js").read_text(encoding="utf-8")
