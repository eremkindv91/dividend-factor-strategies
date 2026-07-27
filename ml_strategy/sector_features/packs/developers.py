PACK = {
    "pack_id": "REAL_ESTATE_DEVELOPERS",
    "label": "Девелоперы",
    "features": [
        "developer_key_rate_level",
        "developer_key_rate_change_60d",
        "developer_rgbi_driver",
        "developer_macro_missing",
    ],
    "approved_sources": ["CBR_KEY_RATE", "MOEX_RGBI"],
    "blocked_sources": ["CBR_MORTGAGE_VINTAGES"],
    "reason": "Факторы ставки проверены; ипотечные vintages с датами публикации пока не подключены.",
}
