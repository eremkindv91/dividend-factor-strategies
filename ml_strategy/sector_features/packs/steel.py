PACK = {
    "pack_id": "STEEL_AND_FERROUS_METALS",
    "label": "Сталь и чёрная металлургия",
    "feature_role": "sector_timing",
    "features": [
        "steel_sector_return_20d",
        "steel_sector_return_60d",
        "steel_sector_relative_20d",
        "steel_sector_volatility_20d",
        "steel_sector_index_missing",
        "steel_fx_driver",
        "steel_fx_driver_missing",
    ],
    "sector_index_source": "MOEX_MOEXMM",
    "feature_prefix": "steel",
    "approved_sources": ["MOEX_IMOEX", "MOEX_MOEXMM", "MOEX_USDRUB"],
    "blocked_sources": ["STEEL_INPUTS_AUDITED"],
    "reason": "Официальный индекс MOEX металлов и валютный фактор обновляются ежедневно; цены сырья пока не подключены.",
}
