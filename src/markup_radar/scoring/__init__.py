"""Scoring & classification (spec §4)."""

from markup_radar.scoring.classifier import classify
from markup_radar.scoring.probability import (
    entry_score,
    rank_alerts,
    score_band,
)
from markup_radar.scoring.score import confidence_markup_start

__all__ = [
    "classify",
    "confidence_markup_start",
    "entry_score",
    "rank_alerts",
    "score_band",
]
