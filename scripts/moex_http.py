#!/usr/bin/env python3
"""Shared bounded HTTP transport for official MOEX ISS builders."""
from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

import requests

TRANSIENT_HTTP = {408, 425, 429, 500, 502, 503, 504}
DEFAULT_UA = (
    "dividend-factor-strategies/moex-builders "
    "(+https://github.com/eremkindv91/dividend-factor-strategies)"
)


class MoexTransportError(RuntimeError):
    """MOEX ISS did not return a usable response after bounded retries."""


class MoexHTTP:
    """Persistent MOEX client with explicit connect/read timeouts and retries."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        user_agent: str = DEFAULT_UA,
        attempts: int = 4,
        connect_timeout: float = 8.0,
        read_timeout: float = 40.0,
        backoff_base: float = 0.7,
        max_backoff: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be positive")
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        self.attempts = attempts
        self.timeout = (connect_timeout, read_timeout)
        self.backoff_base = backoff_base
        self.max_backoff = max_backoff
        self.sleep = sleep
        self.jitter = jitter
        self.logger = logger or (lambda _message: None)

    def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> dict:
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code in TRANSIENT_HTTP:
                    raise requests.HTTPError(
                        f"HTTP {response.status_code}", response=response,
                    )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("MOEX response is not a JSON object")
                return payload
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError,
                    requests.JSONDecodeError, ValueError) as exc:
                last_error = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                transient = status in TRANSIENT_HTTP if status is not None else True
                self.logger(
                    f"attempt={attempt}/{self.attempts} endpoint={url} "
                    f"status={status or type(exc).__name__} transient={str(transient).lower()}"
                )
                if not transient or attempt >= self.attempts:
                    break
                delay = min(self.max_backoff, self.backoff_base * (2 ** (attempt - 1)))
                delay += min(0.5, self.jitter() * 0.35)
                self.sleep(delay)
        raise MoexTransportError(f"MOEX ISS unavailable: {last_error}") from last_error
