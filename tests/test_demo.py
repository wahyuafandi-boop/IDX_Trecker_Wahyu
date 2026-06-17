"""Pastikan skenario demo menghasilkan state yang diharapkan."""

import pytest

from markup_radar.config import load_settings
from markup_radar.demo import classify_snapshot, make_snapshot


@pytest.fixture(scope="module")
def cfg():
    return load_settings()


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("markup", "MARKUP_START"),
        ("accumulation", "ACCUMULATION_ONGOING"),
        ("distribution", "DISTRIBUTION_WARNING"),
        ("neutral", "NEUTRAL"),
    ],
)
def test_snapshot_states(cfg, kind, expected):
    state, conf, _ = classify_snapshot(make_snapshot(kind, kind.upper()), cfg)
    assert state == expected


def test_markup_confidence_high(cfg):
    _, conf, _ = classify_snapshot(make_snapshot("markup", "M"), cfg)
    assert conf >= 70
