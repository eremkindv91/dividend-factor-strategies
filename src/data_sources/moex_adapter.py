from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass
class CompanySecurity:
    secid: str
    short_name: str = ""
    sec_name: str = ""
    board: str = ""
    isin: str = ""
    regnumber: str = ""
    lot_size: int | None = None
    status: str = ""


MOEX_SHARES_SECURITIES_URL = "https://iss.moex.com/iss/engines/stock/markets/shares/boards/{board}/securities.json"


def is_common_stock(secid: str, board: str | None = None, short_name: str = "", sec_name: str = "", status: str = "A") -> bool:
    if not secid:
        return False
    if status and str(status).upper() != "A":
        return False
    upper = str(secid).upper()
    bad_suffixes = ("ETF", "FUND")
    if upper.endswith(bad_suffixes):
        return False
    if re.fullmatch(r"RU[0-9A-Z]{10}", upper):
        return False
    if upper.startswith("XF"):
        return False
    text = f"{short_name} {sec_name}".lower()
    fund_markers = (
        "etf",
        "бпиф",
        "зпиф",
        "пиф",
        "паевой инвестиционный фонд",
        "биржевой фонд",
        "фонд",
    )
    return not any(marker in text for marker in fund_markers)


def parse_moex_securities_payload(payload: dict, board: str = "TQBR") -> list[CompanySecurity]:
    block = payload.get("securities") or {}
    columns = block.get("columns") or []
    rows = block.get("data") or []
    out: list[CompanySecurity] = []
    for values in rows:
        rec = dict(zip(columns, values))
        secid = str(rec.get("SECID") or "").strip().upper()
        short_name = str(rec.get("SHORTNAME") or "").strip()
        sec_name = str(rec.get("SECNAME") or "").strip()
        status = str(rec.get("STATUS") or "").strip().upper()
        if not is_common_stock(secid, board, short_name=short_name, sec_name=sec_name, status=status):
            continue
        lot_size = _int_or_none(rec.get("LOTSIZE"))
        out.append(CompanySecurity(
            secid=secid,
            short_name=short_name,
            sec_name=sec_name,
            board=board,
            isin=str(rec.get("ISIN") or "").strip(),
            regnumber=str(rec.get("REGNUMBER") or "").strip(),
            lot_size=lot_size,
            status=status,
        ))
    return out


def fetch_moex_shares(board: str = "TQBR", session=None, timeout: int = 20) -> list[CompanySecurity]:
    import requests

    client = session or requests
    url = MOEX_SHARES_SECURITIES_URL.format(board=board)
    params = {
        "iss.meta": "off",
        "iss.only": "securities",
        "securities.columns": "SECID,SHORTNAME,SECNAME,ISIN,REGNUMBER,LOTSIZE,STATUS",
    }
    resp = client.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return parse_moex_securities_payload(resp.json(), board=board)


def _int_or_none(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None
