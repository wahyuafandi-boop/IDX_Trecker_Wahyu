"""Confidence score 0-100 untuk MARKUP_START (spec §4)."""

from __future__ import annotations

_DEFAULT_WEIGHTS = {
    "done_ratio": 25,
    "rvol": 20,
    "close_in_range": 15,
    "broker_streak": 20,
    "queue_imbalance": 10,
    "ihsg": 10,
}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def confidence_markup_start(signals: dict, weights: dict | None = None) -> int:
    """Skor 0-100: seberapa kuat sinyal mendukung MARKUP_START.

    Tiap komponen dinormalisasi ke 0..1 lalu dikali bobotnya.
    """
    w = {**_DEFAULT_WEIGHTS, **(weights or {})}
    s = signals

    # Normalisasi tiap sinyal ke 0..1 relatif terhadap zona "ideal".
    norm = {
        "done_ratio": _clamp01((s.get("done_ratio", 0.5) - 0.5) / 0.3),       # 0.5->0, 0.8->1
        "rvol": _clamp01(s.get("rvol", 0.0) / 3.0),                            # 3x -> 1
        "close_in_range": _clamp01(s.get("close_in_range", 0.0)),
        "broker_streak": _clamp01(s.get("broker_net_buy_streak", 0) / 5.0),    # 5 hari -> 1
        "queue_imbalance": _clamp01(s.get("queue_imbalance", 0.0) / 2.0),      # 2x -> 1
        "ihsg": 1.0 if s.get("ihsg_above_ma50", False) else 0.0,
    }

    score = sum(w[k] * norm[k] for k in w)
    return int(round(score))
