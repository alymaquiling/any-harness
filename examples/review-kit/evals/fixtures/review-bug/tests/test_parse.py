from src.parse import parse_ratio


def test_parse_ratio():
    assert parse_ratio("6:3") == 2
