"""Normalisasi --codes (override watchlist) di run_daily."""

import pytest

from markup_radar.config import parse_codes


@pytest.mark.parametrize(
    "tokens,expected",
    [
        (["BBCA", "BBRI"], ["BBCA", "BBRI"]),          # space-separated
        (["BBCA,BBRI,BMRI"], ["BBCA", "BBRI", "BMRI"]),  # comma-separated
        (["BBCA, BBRI"], ["BBCA", "BBRI"]),            # koma + spasi campur
        (["bbca", "bbri"], ["BBCA", "BBRI"]),          # uppercase
        (["BBCA", "bbca", "BBRI"], ["BBCA", "BBRI"]),  # dedup, jaga urutan
        ([","], []),                                    # token sampah
        ([], []),                                        # tidak ada kode
    ],
)
def test_parse_codes(tokens, expected):
    assert parse_codes(tokens) == expected
