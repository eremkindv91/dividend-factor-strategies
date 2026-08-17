import json

from scripts import moex_instruments


def test_describe_can_use_expired_metadata_without_network(tmp_path, monkeypatch):
    cache_path = tmp_path / "moex_instruments_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": {
                    "SBER": {
                        "secid": "SBER",
                        "found": True,
                        "short_name": "Sberbank",
                        "instrument_type": "equity_ordinary",
                        "_cached_at": 1,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(moex_instruments, "CACHE", str(cache_path))

    def fail_if_called(_url):
        raise AssertionError("expired metadata cache unexpectedly triggered a network call")

    monkeypatch.setattr(moex_instruments, "_http_json", fail_if_called)

    result = moex_instruments.describe("SBER", allow_stale_cache=True)

    assert result["secid"] == "SBER"
    assert result["instrument_type"] == "equity_ordinary"
    assert "_cached_at" not in result
