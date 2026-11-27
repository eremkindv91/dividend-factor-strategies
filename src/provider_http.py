"""JSON requests to known provider endpoints with credential-safe errors."""
from __future__ import annotations

import requests


class ProviderError(RuntimeError):
    """An API error safe to show in a CI log."""


def request_json(method: str, url: str, operation: str, *, billing_errors: dict[str, str] | None = None,
                 **kwargs) -> dict:
    try:
        response = requests.request(method, url, timeout=(8, 90), allow_redirects=False, **kwargs)
    except requests.RequestException as exc:
        raise ProviderError(f"{operation}: {type(exc).__name__}") from None
    if not 200 <= response.status_code < 300:
        message = f"{operation}: HTTP {response.status_code}"
        if response.status_code == 402 and billing_errors:
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                code = str(body.get("code"))
                if code in billing_errors:
                    message += f" · {code} · {billing_errors[code]}"
        raise ProviderError(message)
    try:
        data = response.json()
    except ValueError:
        raise ProviderError(f"{operation}: ответ не в формате JSON") from None
    if not isinstance(data, dict):
        raise ProviderError(f"{operation}: неожиданный формат ответа")
    return data
