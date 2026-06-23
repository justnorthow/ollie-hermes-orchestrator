import pytest
from src.api.geo import normalize_region_key

CASES = [
    ("county", "Williamson", "williamson"),
    ("county", "Williamson County", "williamson"),
    ("county", "Williamson County, TX", "williamson"),
    ("city", "Round Rock", "round rock"),
    ("city", "Round Rock, TX", "round rock"),
    ("zip", "78664", "78664"),
    ("zip", "78664-1234", "78664"),
    ("zip", "Zip Code: 78664", "78664"),
    ("county", "", ""),
]


@pytest.mark.parametrize("rt,value,expected", CASES)
def test_normalize_region_key(rt, value, expected):
    assert normalize_region_key(rt, value) == expected
