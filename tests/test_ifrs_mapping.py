from pathlib import Path

from src.normalization.ifrs_mapper import load_ifrs_mapping, map_line_item


def test_load_and_map_ifrs_line_item():
    mapping = load_ifrs_mapping(Path("data/mapping/ifrs_line_items.yaml"))

    assert map_line_item("Выручка", mapping) == "revenue"
    assert map_line_item("Net profit", mapping) == "net_income"
    assert map_line_item("Invented EBITDA", mapping) is None
