"""Backtesting & threshold tuning (spec Phase 5).

replay()   -> klasifikasi histori per hari + forward return.
summarize()-> laporan akurasi per state.
tune()     -> grid-search satu threshold untuk maksimalkan hit-rate MARKUP_START.
"""

from markup_radar.backtest.dataset import HistoricalDataset, load_history
from markup_radar.backtest.engine import replay
from markup_radar.backtest.metrics import summarize, tune

__all__ = ["HistoricalDataset", "load_history", "replay", "summarize", "tune"]
