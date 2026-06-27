from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CompanySecurity:
    secid: str
    short_name: str = ""
    board: str = ""


def is_common_stock(secid: str, board: str | None = None) -> bool:
    bad_suffixes = ("ETF", "FUND")
    return bool(secid) and not secid.upper().endswith(bad_suffixes)

