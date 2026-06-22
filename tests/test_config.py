"""Normalisasi --codes (override watchlist) di run_daily."""

import pytest

from markup_radar.config import load_codes_file, parse_codes


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


def test_load_codes_file(tmp_path):
    f = tmp_path / "watchlist_today.txt"
    f.write_text(
        "# komentar header\n"
        "BBCA\n"
        "bbri, BMRI   # campur koma/spasi + komentar inline\n"
        "\n"                 # baris kosong
        "BBCA\n"             # duplikat -> di-dedup
        "   \n",
        encoding="utf-8",
    )
    assert load_codes_file(f) == ["BBCA", "BBRI", "BMRI"]


def test_load_codes_file_missing(tmp_path):
    with pytest.raises(OSError):
        load_codes_file(tmp_path / "tidak_ada.txt")
