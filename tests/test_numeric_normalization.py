from src.normalization.numeric import normalize_numeric_value


def test_normalize_russian_number_with_multiplier():
    assert normalize_numeric_value("1 234,5 млн", 1_000_000) == 1_234_500_000


def test_normalize_parentheses_negative_and_sign_convention():
    assert normalize_numeric_value("(12,5)", 1) == -12.5
    assert normalize_numeric_value("(12,5)", 1, sign_convention="positive") == 12.5


def test_normalize_empty_tokens():
    assert normalize_numeric_value("—") is None
    assert normalize_numeric_value(None) is None
