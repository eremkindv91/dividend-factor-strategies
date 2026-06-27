from __future__ import annotations


def normalize_issuer_name(name: str) -> str:
    s = (name or "").lower()
    for token in ("пао", "ао", "мкпао", "гк", '"', "«", "»"):
        s = s.replace(token, " ")
    return " ".join(s.replace("-", " ").split())

