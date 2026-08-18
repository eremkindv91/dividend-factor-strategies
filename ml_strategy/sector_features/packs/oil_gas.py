PACK = {
    "pack_id": "OIL_AND_GAS",
    "label": "Нефть и газ",
    "feature_role": "sector_timing",
    "features": [
        "oil_sector_return_20d",
        "oil_sector_return_60d",
        "oil_sector_relative_20d",
        "oil_sector_volatility_20d",
        "oil_sector_index_missing",
        "oil_fx_driver",
        "oil_fx_driver_missing",
    ],
    "sector_index_source": "MOEX_MOEXOG",
    "feature_prefix": "oil",
    "approved_sources": ["MOEX_IMOEX", "MOEX_MOEXOG", "MOEX_USDRUB"],
    "blocked_sources": ["BRENT_URALS_AUDITED"],
    "reason": "Официальный индекс MOEX нефти и газа и валютный фактор обновляются ежедневно; Brent/Urals пока не подключён.",
}
