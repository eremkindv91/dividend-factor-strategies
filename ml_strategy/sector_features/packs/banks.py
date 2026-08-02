PACK = {
    "pack_id": "BANKS_AND_FINANCIALS",
    "label": "Банки",
    "feature_role": "sector_timing",
    "features": [
        "bank_key_rate_level",
        "bank_key_rate_change_60d",
        "bank_rgbi_driver",
        "bank_macro_missing",
    ],
    "approved_sources": ["CBR_KEY_RATE", "MOEX_RGBI"],
    "blocked_sources": [],
    "reason": "Доступны официальные факторы ключевой ставки и рынка ОФЗ.",
}
