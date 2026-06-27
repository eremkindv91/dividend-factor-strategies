from src.io_utils import write_csv


def test_write_csv_uses_lf_line_endings(tmp_path):
    path = tmp_path / "sample.csv"

    write_csv(path, [{"ticker": "TEST"}], ["ticker"])

    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw == b"ticker\nTEST\n"
