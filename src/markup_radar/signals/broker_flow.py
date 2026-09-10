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


def flow_price_compatibility(
    dated_net: Sequence[tuple[str, float]],
    ohlcv: pd.DataFrame,
    *,
    min_overlap: int = 8,
) -> float | None:
    """S11 'compatibility' (konsep tape-reading/NeoBDM): korelasi Pearson antara
    net harian broker akumulator (agregat top-N, S3) dan return harian saham.

    Tinggi (>~0.5) = yang akumulasi memang penggerak harga -> sinyal lebih layak
    dipercaya (kasus TINS ~70%). Rendah/negatif = broker borong tapi harga tak
    mengikuti (kasus BUMI: akum asing, harga diam) -> sinyal lemah. Join by-date
    (bukan positional) karena tanggal broker bisa bolong vs OHLCV.

    Return None bila overlap tanggal < min_overlap atau salah satu deret nyaris
    konstan (korelasi tak bermakna) — None berarti "tak ada bacaan", bukan 0.
    """
    if not dated_net or ohlcv is None or ohlcv.empty or "date" not in ohlcv:
        return None
    net_by_date = {str(d)[:10]: float(v) for d, v in dated_net}
    px = ohlcv.sort_values("date")
    rets = px["close"].pct_change()
    pairs = [
        (net_by_date[str(d)[:10]], float(r))
        for d, r in zip(px["date"], rets)
        if str(d)[:10] in net_by_date and pd.notna(r)
    ]
    if len(pairs) < min_overlap:
        return None
    s_net = pd.Series([p[0] for p in pairs])
    s_ret = pd.Series([p[1] for p in pairs])
    if s_net.std() == 0 or s_ret.std() == 0:
        return None
    corr = s_net.corr(s_ret)
    return None if pd.isna(corr) else round(float(corr), 3)


def broker_turning_net_sell(daily_net: Sequence[float], lookback: int = 3) -> bool:
    """Indikasi broker besar berbalik jual: dari net buy menjadi net sell baru-baru ini."""
    net = list(daily_net)
    if len(net) < lookback + 1:
        return False
    earlier = net[-(lookback + 1):-1]
    latest = net[-1]
    was_accumulating = sum(earlier) > 0
    return was_accumulating and latest < 0
