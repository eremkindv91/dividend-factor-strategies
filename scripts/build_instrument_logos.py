#!/usr/bin/env python3
"""Build a local, production-safe logo registry from the T-Invest catalogue.

The token is sent only to the official instruments API. Logo PNGs are downloaded
from the documented T-Invest brand CDN into a temporary deploy directory; browsers
never contact either service. A failed build writes nothing, so deploy can retain
the previously published registry and images.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


API_ROOT = "https://invest-public-api.tbank.ru/rest/tinkoff.public.invest.api.contract.v1.InstrumentsService"
SDK_TARGET = "invest-public-api.tbank.ru"
BRAND_CDN = "https://invest-brands.cdn-tinkoff.ru"
REQUEST_TIMEOUT = (5, 30)
MAX_IMAGE_BYTES = 300_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PREFERRED_BOARDS = {"TQBR": 5, "TQTF": 4, "TQPI": 3, "TQIF": 2}
SAFE_TICKER = re.compile(r"^[A-Z0-9._-]{1,24}$")
SAFE_LOGO_NAME = re.compile(r"^[A-Za-z0-9._-]+\.png$")


class LogoBuildError(RuntimeError):
    pass


def _post(session: requests.Session, token: str, method: str) -> list[dict[str, Any]]:
    url = f"{API_ROOT}/{method}"
    response = session.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"instrumentStatus": "INSTRUMENT_STATUS_ALL"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("instruments") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise LogoBuildError(f"{method}: invalid response contract")
    return [row for row in rows if isinstance(row, dict)]


def _sdk_row(item: Any, catalog_type: str) -> dict[str, Any]:
    brand = getattr(item, "brand", None)
    return {
        "ticker": str(getattr(item, "ticker", "") or ""),
        "name": str(getattr(item, "name", "") or ""),
        "class_code": str(getattr(item, "class_code", "") or ""),
        "currency": str(getattr(item, "currency", "") or ""),
        "country_of_risk": str(getattr(item, "country_of_risk", "") or ""),
        "brand": {"logo_name": str(getattr(brand, "logo_name", "") or "")},
        "_catalog_type": catalog_type,
    }


def _sdk_catalogue(token: str) -> list[dict[str, Any]]:
    """Use the same official gRPC transport as the working dividend collector."""
    os.environ.setdefault("SSL_TBANK_VERIFY", "True")
    try:
        from t_tech.invest import Client, InstrumentStatus
    except ModuleNotFoundError as exc:
        raise LogoBuildError("t_tech_invest_sdk_missing") from exc

    status = InstrumentStatus.INSTRUMENT_STATUS_ALL
    with Client(token, target=SDK_TARGET, app_name="dividend-factor-strategies") as client:
        shares = client.instruments.shares(instrument_status=status).instruments
        funds = client.instruments.etfs(instrument_status=status).instruments
    return ([_sdk_row(row, "equity") for row in shares]
            + [_sdk_row(row, "fund") for row in funds])


def _universe(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("tickers") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise LogoBuildError("universe must contain a tickers array")
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        if SAFE_TICKER.fullmatch(ticker):
            result[ticker] = str(row.get("name") or ticker).strip() or ticker
    if not result:
        raise LogoBuildError("universe is empty")
    return result


def _value(row: dict, snake: str, camel: str | None = None):
    return row.get(snake) if row.get(snake) is not None else row.get(camel or snake)


def _candidate_score(row: dict) -> tuple[int, int, int, str]:
    board = str(_value(row, "class_code", "classCode") or "").upper()
    currency = str(row.get("currency") or "").upper()
    country = str(_value(row, "country_of_risk", "countryOfRisk") or "").upper()
    return (PREFERRED_BOARDS.get(board, 0), int(currency == "RUB"), int(country == "RU"), board)


def _logo_name(row: dict) -> str:
    brand = row.get("brand")
    if not isinstance(brand, dict):
        return ""
    name = str(_value(brand, "logo_name", "logoName") or "").strip()
    return name if SAFE_LOGO_NAME.fullmatch(name) else ""


def select_catalogue_rows(rows: list[dict], universe: dict[str, str]) -> dict[str, dict]:
    selected: dict[str, dict] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker not in universe or not _logo_name(row):
            continue
        if ticker not in selected or _candidate_score(row) > _candidate_score(selected[ticker]):
            selected[ticker] = row
    return selected


def _png_dimensions(content: bytes) -> tuple[int, int]:
    if len(content) < 24 or not content.startswith(PNG_SIGNATURE) or content[12:16] != b"IHDR":
        raise LogoBuildError("invalid_png")
    width, height = struct.unpack(">II", content[16:24])
    if not (8 <= width <= 640 and 8 <= height <= 640):
        raise LogoBuildError("invalid_png_dimensions")
    return width, height


def _download_png(logo_name: str) -> tuple[bytes, str]:
    stem = logo_name[:-4]
    source = f"{BRAND_CDN}/{quote(stem + 'x160.png', safe='')}"
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            # Deliberately do not reuse API authorization headers for the public CDN.
            response = requests.get(source, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            response.raise_for_status()
            content = response.content
            if len(content) > MAX_IMAGE_BYTES:
                raise LogoBuildError("image_too_large")
            _png_dimensions(content)
            return content, source
        except (requests.RequestException, LogoBuildError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    raise LogoBuildError(type(last_error).__name__ if last_error else "download_failed")


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_text(path: Path, content: str) -> None:
    _atomic_bytes(path, content.encode("utf-8"))


def _previous_registry(path: Path | None, universe: dict[str, str]) -> dict[str, dict]:
    if not path or not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    match = re.search(r"Object\.freeze\((\{.*\})\)\s*;?", text, re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except ValueError:
        return {}
    clean: dict[str, dict] = {}
    for ticker, row in payload.items() if isinstance(payload, dict) else []:
        path_value = str(row.get("logo_path") or "") if isinstance(row, dict) else ""
        if ticker in universe and re.fullmatch(r"assets/instruments/companies/[a-z0-9._-]+\.png", path_value):
            clean[ticker] = row
    return clean


def build(
    universe_path: Path,
    output_dir: Path,
    token: str,
    session: requests.Session | None = None,
    previous_registry: Path | None = None,
) -> dict:
    if not token:
        return {"status": "disabled", "universe": 0, "catalogue_matches": 0, "downloaded": 0, "failed": 0}
    universe = _universe(universe_path)
    if session is None:
        rows = _sdk_catalogue(token)
    else:
        shares = [{**row, "_catalog_type": "equity"} for row in _post(session, token, "Shares")]
        funds = [{**row, "_catalog_type": "fund"} for row in _post(session, token, "Etfs")]
        rows = shares + funds
    selected = select_catalogue_rows(rows, universe)
    registry = _previous_registry(previous_registry, universe)
    fresh_downloads = 0
    failures: dict[str, str] = {}
    image_dir = output_dir / "assets" / "instruments" / "companies"
    for ticker, row in sorted(selected.items()):
        try:
            content, source = _download_png(_logo_name(row))
            filename = f"{ticker.lower()}.png"
            _atomic_bytes(image_dir / filename, content)
            registry[ticker] = {
                "secid": ticker,
                "type": row.get("_catalog_type") or "equity",
                "name": universe[ticker],
                "logo_path": f"assets/instruments/companies/{filename}",
                "logo_source": source,
                "logo_status": "broker_catalog",
                "updated_at": date.today().isoformat(),
            }
            fresh_downloads += 1
        except Exception as exc:  # Only sanitized codes are written; response bodies and token never are.
            failures[ticker] = str(exc)[:80] or type(exc).__name__

    if not registry:
        raise LogoBuildError("no valid logos downloaded")
    output_dir.mkdir(parents=True, exist_ok=True)
    compact = json.dumps(registry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _atomic_text(output_dir / "instrument_logos.js", f"window.InstrumentLogoRegistry=Object.freeze({compact});\n")
    manifest = {
        "schema_version": 1,
        "generated_at": date.today().isoformat(),
        "source": "T-Invest instrument catalogue and brand CDN",
        "source_url": "https://developer.tbank.ru/invest/services/instruments/faq_instruments",
        "universe_count": len(universe),
        "catalogue_matches": len(selected),
        "downloaded": fresh_downloads,
        "registry_count": len(registry),
        "failed": len(failures),
        "failures": failures,
        "assets": list(registry.values()),
    }
    _atomic_text(
        output_dir / "assets" / "instruments" / "company_manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return {"status": "ok", "universe": len(universe), "catalogue_matches": len(selected), "downloaded": fresh_downloads, "registry_count": len(registry), "failed": len(failures)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", type=Path, default=Path("site/data.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--previous-registry", type=Path)
    args = parser.parse_args()
    try:
        summary = build(
            args.universe,
            args.output_dir,
            os.environ.get("TINVEST_TOKEN", ""),
            previous_registry=args.previous_registry,
        )
    except Exception as exc:
        print(f"[instrument-logos] failed: {type(exc).__name__}: {str(exc)[:120]}")
        return 1
    print("[instrument-logos] " + json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
