"""S3 Broker Net Flow, S4 Broker Concentration (spec §3)."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd


def broker_net_buy_streak(daily_net: Sequence[float]) -> int:
    """S3: panjang streak net-buy berturut-turut paling akhir.

    daily_net urut kronologis (lama -> baru). Hitung berapa hari terakhir
    net value-nya positif tanpa putus.
    """
    streak = 0
    for net in reversed(list(daily_net)):
        if net > 0:
            streak += 1
        else:
            break
    return streak


def broker_concentration(broker_summary: pd.DataFrame, top_n: int = 5) -> float:
    """S4: porsi net buy top-N broker terhadap total net buy positif.

    Tinggi (mendekati 1.0) + konsisten = aktivitas terkoordinasi (bandar).
    broker_summary: DataFrame dengan kolom 'net_value'.
    """
    if broker_summary.empty or "net_value" not in broker_summary:
        return 0.0
    buyers = broker_summary[broker_summary["net_value"] > 0]
    total_buy = buyers["net_value"].sum()
    if total_buy <= 0:
        return 0.0
    top = buyers.nlargest(top_n, "net_value")["net_value"].sum()
    return float(top / total_buy)


def broker_turning_net_sell(daily_net: Sequence[float], lookback: int = 3) -> bool:
    """Indikasi broker besar berbalik jual: dari net buy menjadi net sell baru-baru ini."""
    net = list(daily_net)
    if len(net) < lookback + 1:
        return False
    earlier = net[-(lookback + 1):-1]
    latest = net[-1]
    was_accumulating = sum(earlier) > 0
    return was_accumulating and latest < 0
