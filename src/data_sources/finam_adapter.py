"""Read-only market quotes from Finam Trade API."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from src.provider_http import ProviderError, request_json

API = "https://api.finam.ru/v1"


@dataclass(frozen=True)
class FinamQuote:
    symbol: str
    timestamp: datetime
    last: Decimal


def latest_quote(secret: str, symbol: str = "SBER@MISX") -> FinamQuote:
    secret = secret.strip()
    if not secret:
        raise ProviderError("Finam: отсутствует FINAM_TRADE_API_KEY")
    if secret.startswith("tapi_ak_"):
        raise ProviderError("Finam: сохранён Access Key, для авторизации требуется Secret Key")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,39}@[A-Z0-9]{4}", symbol):
        raise ValueError("Finam: ожидается символ в формате SBER@MISX")
    session = request_json("POST", f"{API}/sessions", "Finam: авторизация", json={"secret": secret})
    token = session.get("token")
    if not isinstance(token, str) or not token:
        raise ProviderError("Finam: ответ авторизации не содержит access token")
    details = request_json("POST", f"{API}/sessions/details", "Finam: права ключа", json={"token": token})
    if details.get("readonly") is not True:
        raise ProviderError("Finam: требуется токен с подтверждённым режимом «только чтение»")
    result = request_json(
        "GET", f"{API}/instruments/{symbol}/quotes/latest", "Finam: котировка",
        headers={"Authorization": f"Bearer {token}"},
    )
    quote = result.get("quote")
    if not isinstance(quote, dict) or (quote.get("symbol") or result.get("symbol")) != symbol:
        raise ProviderError("Finam: отсутствует котировка запрошенного инструмента")
    try:
        timestamp = datetime.fromisoformat(quote["timestamp"].replace("Z", "+00:00"))
        last = Decimal(quote["last"]["value"])
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation):
        raise ProviderError("Finam: котировка без корректной цены или времени") from None
    if timestamp.utcoffset() is None or not last.is_finite():
        raise ProviderError("Finam: котировка без корректной цены или времени")
    return FinamQuote(symbol, timestamp, last)
