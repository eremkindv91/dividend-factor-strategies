"""JSON requests to known provider endpoints with credential-safe errors."""
from __future__ import annotations

import requests


class ProviderError(RuntimeError):
    """An API error safe to show in a CI log."""


def request_json(method: str, url: str, operation: str, **kwargs) -> dict:
    try:
        response = requests.request(method, url, timeout=(8, 90), allow_redirects=False, **kwargs)
    except requests.RequestException as exc:
        raise ProviderError(f"{operation}: {type(exc).__name__}") from None
    if not 200 <= response.status_code < 300:
        raise ProviderError(f"{operation}: HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise ProviderError(f"{operation}: ответ не в формате JSON") from None
    if not isinstance(data, dict):
        raise ProviderError(f"{operation}: неожиданный формат ответа")
    return data
