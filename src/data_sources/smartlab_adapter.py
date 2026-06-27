from __future__ import annotations


def smartlab_source_url(ticker: str) -> str:
    return f"https://smart-lab.ru/q/{str(ticker).upper()}/f/y/"

