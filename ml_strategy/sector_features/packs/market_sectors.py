from __future__ import annotations


def _index_pack(pack_id: str, label: str, prefix: str, source_id: str) -> dict:
    return {
        "pack_id": pack_id,
        "label": label,
        "feature_role": "sector_timing",
        "features": [
            f"{prefix}_sector_return_20d",
            f"{prefix}_sector_return_60d",
            f"{prefix}_sector_relative_20d",
            f"{prefix}_sector_volatility_20d",
            f"{prefix}_sector_index_missing",
        ],
        "sector_index_source": source_id,
        "feature_prefix": prefix,
        "approved_sources": ["MOEX_IMOEX", source_id],
        "blocked_sources": [],
        "reason": "Официальный отраслевой индекс MOEX обновляется ежедневно и проходит отдельную OOS-проверку.",
    }


INDEX_ONLY_PACKS = (
    _index_pack("ELECTRIC_UTILITIES", "Электроэнергетика", "utilities", "MOEX_MOEXEU"),
    _index_pack("CONSUMER_SECTOR", "Потребительский сектор", "consumer", "MOEX_MOEXCN"),
    _index_pack("INFORMATION_TECHNOLOGY", "Информационные технологии", "it", "MOEX_MOEXIT"),
    _index_pack("TELECOMMUNICATIONS", "Телекоммуникации", "telecom", "MOEX_MOEXTL"),
    _index_pack("TRANSPORT", "Транспорт", "transport", "MOEX_MOEXTN"),
    _index_pack("CHEMICALS_AND_PETROCHEMICALS", "Химия и нефтехимия", "chemicals", "MOEX_MOEXCH"),
    _index_pack("FINANCIAL_SERVICES", "Финансовые сервисы", "finance", "MOEX_MOEXFN"),
)
