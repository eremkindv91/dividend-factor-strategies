from __future__ import annotations


UNIT_MULTIPLIERS = {
    "rub": 1,
    "rur": 1,
    "₽": 1,
    "руб": 1,
    "тыс руб": 1_000,
    "руб тыс": 1_000,
    "млн руб": 1_000_000,
    "руб млн": 1_000_000,
    "rub million": 1_000_000,
    "млрд руб": 1_000_000_000,
    "rub billion": 1_000_000_000,
}


def unit_multiplier(unit: str | None) -> int:
    if not unit:
        return 1
    return UNIT_MULTIPLIERS.get(str(unit).strip().lower(), 1)

