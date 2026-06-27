from src.data_sources.moex_adapter import is_common_stock, parse_moex_securities_payload


def test_parse_moex_securities_filters_funds_and_keeps_common_shares():
    payload = {
        "securities": {
            "columns": ["SECID", "SHORTNAME", "SECNAME", "ISIN", "REGNUMBER", "LOTSIZE", "STATUS"],
            "data": [
                ["SBER", "Сбербанк", "Сбербанк России ПАО ао", "RU0009029540", "10301481B", 10, "A"],
                ["SBERP", "Сбербанк-п", "Сбербанк России ПАО ап", "RU0009029557", "20301481B", 10, "A"],
                ["AKAI", "AKAI ETF", "БПИФ Антиинфляционный", "RU000A109PQ9", "6527", 1, "A"],
                ["BAD", "Bad", "Bad issuer", "RU0000000000", "000", 1, "D"],
            ],
        }
    }

    rows = parse_moex_securities_payload(payload, board="TQBR")

    assert [r.secid for r in rows] == ["SBER", "SBERP"]
    assert rows[0].sec_name == "Сбербанк России ПАО ао"
    assert rows[0].board == "TQBR"


def test_is_common_stock_rejects_etf_and_funds_by_name():
    assert is_common_stock("SBER", "TQBR", short_name="Сбербанк", sec_name="Сбербанк России ПАО ао")
    assert not is_common_stock("AKAI", "TQBR", short_name="AKAI ETF", sec_name="БПИФ Антиинфляционный")
    assert not is_common_stock("FUND", "TQBR", short_name="Фонд", sec_name="Биржевой паевой инвестиционный фонд")


def test_is_common_stock_rejects_non_equity_tqbr_products_by_secid_pattern():
    assert not is_common_stock("RU000A103B62", "TQBR", short_name="РенДохПРО", sec_name="Рентный доход ПРО")
    assert not is_common_stock("XFMAGR", "TQBR", short_name="ФАлгоритм", sec_name="Финам Алгоритм роста")
    assert is_common_stock("VEON-RX", "TQBR", short_name="VEON", sec_name="VEON Ltd. ORD SHS")
