from decimal import Decimal
from pathlib import Path

import pytest

from src import provider_http
from src.data_sources import finam_adapter
from src.ocr import mistral_ocr


def test_finam_accepts_only_readonly_tokens_and_keeps_exact_quote(monkeypatch):
    calls = []
    replies = iter([
        {"token": "test-access-token"},
        {"readonly": True, "account_ids": ["private-account"]},
        {"quote": {"symbol": "SBER@MISX", "timestamp": "2026-11-27T11:24:26Z",
                   "last": {"value": "288.0700"}}},
    ])

    def request(method, url, operation, **kwargs):
        calls.append((method, url, kwargs))
        return next(replies)

    monkeypatch.setattr(finam_adapter, "request_json", request)
    result = finam_adapter.latest_quote(" test-key\n")
    assert result.last == Decimal("288.0700")
    assert str(result.last) == "288.0700"
    assert [method for method, *_ in calls] == ["POST", "POST", "GET"]
    assert calls[0][2]["json"] == {"secret": "test-key"}
    assert calls[-1][2]["headers"] == {"Authorization": "Bearer test-access-token"}


def test_finam_rejects_trading_permission_before_quote_request(monkeypatch):
    replies = iter([{"token": "test-access-token"}, {"readonly": False}])
    monkeypatch.setattr(finam_adapter, "request_json", lambda *a, **k: next(replies))
    with pytest.raises(provider_http.ProviderError, match="только чтение"):
        finam_adapter.latest_quote("test-key")


def test_finam_reports_access_key_before_attempting_secret_exchange(monkeypatch):
    monkeypatch.setattr(finam_adapter, "request_json", lambda *a, **k: pytest.fail("Access Key sent"))
    with pytest.raises(provider_http.ProviderError, match="требуется Secret Key"):
        finam_adapter.latest_quote("tapi_ak_example")


def test_mistral_sends_only_selected_pages_for_processing(monkeypatch):
    def request(method, url, operation, **kwargs):
        assert method == "POST"
        assert url == "https://api.mistral.ai/v1/ocr"
        payload = kwargs["json"]
        assert payload["pages"] == [0]
        assert payload["document"]["document_url"].startswith("data:application/pdf;base64,")
        assert not payload["include_image_base64"]
        return {"model": "mistral-ocr-latest", "pages": [{"index": 0, "markdown": "# Page"}]}

    monkeypatch.setattr(mistral_ocr, "request_json", request)
    pdf = Path("results/ml_forecast_v5/fig_backtest_annual_returns.pdf")
    assert mistral_ocr.MistralOCRBackend(api_key="").extract_document(pdf).extraction_method.endswith("skipped")
    result = mistral_ocr.MistralOCRBackend(api_key="test-key").extract_document(pdf, pages=[0])
    assert result.markdown == "# Page"
    assert len(result.source_sha256) == 64


def test_provider_rejects_redirects_without_logging_response_body(monkeypatch):
    class Redirect:
        status_code = 307

        def json(self):
            pytest.fail("A redirect response body must not be parsed")

    def request(method, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        return Redirect()

    monkeypatch.setattr(provider_http.requests, "request", request)
    with pytest.raises(provider_http.ProviderError, match=r"^Diagnostic: HTTP 307$"):
        provider_http.request_json("GET", "https://api.finam.ru", "Diagnostic")


def test_mistral_billing_error_reports_only_known_code(monkeypatch):
    class BillingResponse:
        status_code = 402

        def json(self):
            return {"code": 2308, "message": "provider details that must stay private"}

    monkeypatch.setattr(provider_http.requests, "request", lambda *a, **kw: BillingResponse())
    with pytest.raises(provider_http.ProviderError, match=r"^Mistral OCR: HTTP 402 · 2308 · закончились предоплаченные кредиты$"):
        provider_http.request_json(
            "POST", "https://api.mistral.ai/v1/ocr", "Mistral OCR", billing_errors=mistral_ocr.BILLING_ERRORS,
        )
