"""Normalisasi broker summary (S3 net flow, S4 concentration) & order book (S5)."""

from __future__ import annotations

import pandas as pd

from markup_radar.ingest.client import InvezgoClient


def _pick(row: dict, *keys: str, default=None):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def fetch_broker_summary(
    client: InvezgoClient, code: str, date_from: str, date_to: str
) -> pd.DataFrame:
    """Broker summary -> DataFrame[broker, buy_value, sell_value, net_value, net_volume].

    Satu baris per broker (akumulasi rentang date_from..date_to). Shape Invezgo:
    list [{code, name, buy_value, sell_value, net_value, net_volume, ...}] —
    nilai berupa STRING, jadi di-coerce ke float.
    """
    raw = client.broker_summary_stock(code, date_from, date_to)
    rows = raw if isinstance(raw, list) else raw.get("brokers", raw.get("items", []))

    records = []
    for r in rows:
        buy_val = _f(_pick(r, "buy_value", "buyValue", "BuyValue"))
        sell_val = _f(_pick(r, "sell_value", "sellValue", "SellValue"))
        net_raw = _pick(r, "net_value", "netValue", "NetValue")
        records.append(
            {
                "broker": _pick(r, "code", "broker", "brokerCode", default="?"),
                "buy_value": buy_val,
                "sell_value": sell_val,
                "net_value": _f(net_raw) if net_raw is not None else buy_val - sell_val,
                "net_volume": _f(_pick(r, "net_volume", "netVolume", "NetVolume")),
            }
        )
    return pd.DataFrame.from_records(records)


def fetch_broker_daily_net_dated(
    client: InvezgoClient, code: str, date_from: str, date_to: str, *, top_n: int = 5
) -> list[tuple[str, float]]:
    """Net broker besar per hari sebagai (date, net) kronologis — 1 API call.

    Pakai inventory-chart. Shape Invezgo:
        {price:[...], broker:[{broker, name, data:[{date, value}]}]}
    `value` = net KUMULATIF per broker (negatif = distribusi). Langkah:
      1. ambil top-N broker akumulator (nilai kumulatif akhir terbesar),
      2. jumlahkan kumulatif top-N per tanggal (forward-fill broker yg bolong),
      3. delta antar-hari = net harian (positif = akumulasi).
    Return tanggal asli broker agar caller bisa join by-date (bukan align
    positional ke OHLCV yang rapuh). Return [] bila data tak tersedia.
    """
    try:
        raw = client.inventory_chart_stock(code, date_from, date_to)
    except Exception:  # noqa: BLE001 — streak opsional, jangan gagalkan saham
        return []

    brokers = raw.get("broker", []) if isinstance(raw, dict) else []
    per_broker: list[list[tuple[str, float]]] = []
    for b in brokers:
        pts = [
            (str(_pick(d, "date", "time", default="")), _f(_pick(d, "value", "net", "netValue")))
            for d in (b.get("data") or [])
        ]
        if pts:
            per_broker.append(sorted(pts, key=lambda x: x[0]))

    if not per_broker:
        return []

    # top-N akumulator berdasar kumulatif akhir.
    per_broker.sort(key=lambda pts: pts[-1][1], reverse=True)
    top = per_broker[:top_n]

    # agregat kumulatif top-N per tanggal — FORWARD-FILL ke sumbu tanggal gabungan.
    # Tanpa ini, broker yang bolong 1 tanggal bikin kumulatif agregat turun semu
    # -> delta harian negatif palsu -> sinyal S3 (streak/turning) bisa flip.
    # Hari tanpa baris = tidak ada perubahan posisi broker -> kumulatif terakhir
    # dibawa terus (delta 0), bukan dianggap turun ke 0.
    dates = sorted({d for pts in top for d, _ in pts})
    if not dates:
        return []
    agg: dict[str, float] = {d: 0.0 for d in dates}
    for pts in top:
        pdict = dict(pts)
        last = 0.0
        for d in dates:
            if d in pdict:
                last = pdict[d]
            agg[d] += last

    cum = [agg[d] for d in dates]
    # delta harian; cum[0] = net hari pertama itu sendiri. Diverifikasi 2026-06-21:
    # kumulatif inventory-chart RESET di `from` (nilai hari pertama seukuran delta
    # harian, bukan posisi triliunan), jadi cum[0] adalah net pembuka yang valid —
    # bukan posisi lama yang bocor (temuan review #5 -> refuted oleh data).
    deltas = [cum[0]] + [cum[i] - cum[i - 1] for i in range(1, len(cum))]
    return list(zip(dates, deltas))


def fetch_broker_daily_net(
    client: InvezgoClient, code: str, date_from: str, date_to: str, *, top_n: int = 5
) -> list[float]:
    """Net broker besar per hari (list nilai kronologis) untuk S3 streak.

    Thin wrapper di atas fetch_broker_daily_net_dated; dipakai run_daily yang
    cuma butuh urutan net (streak/turning tak perlu tanggal). Backtest pakai
    versi *_dated agar bisa join by-date.
    """
    return [net for _, net in fetch_broker_daily_net_dated(client, code, date_from, date_to, top_n=top_n)]


def _order_levels(side) -> list[dict[str, float]]:
    """Parse list level order book -> [{price, lot, freq}] urut sesuai API
    (asumsi level terbaik di indeks 0). Ambil value dgn key berakhiran
    price/lot/freq (case-insensitive) -> robust utk penomoran bidN/offerN."""
    out: list[dict[str, float]] = []
    for level in side or []:
        if not isinstance(level, dict):
            continue
        price = lot = freq = 0.0
        for k, v in level.items():
            kl = str(k).lower()
            if kl.endswith("price"):
                price = _f(v)
            elif kl.endswith("freq"):
                freq = _f(v)
            elif kl.endswith("lot"):
                lot = _f(v)
        out.append({"price": price, "lot": lot, "freq": freq})
    return out


def fetch_closing_queue(
    client: InvezgoClient, code: str, *, top_levels: int = 4
) -> dict[str, float]:
    """Order book -> antrian closing (S5) + komposisi top-N (tape-reading).

    Shape Invezgo: {code, bid:[{bid1price,bid1lot,bid1freq}], offer:[{...}]}.
    Mengembalikan DUA lapis informasi:
      - bid_volume/offer_volume : jumlah lot SEMUA level (kontrak lama, dipakai
        path EOD run_daily + backtest — JANGAN diubah semantiknya).
      - *_top_lot/*_top_freq + *_lot_per_order : agregat `top_levels` baris teratas
        ("yang nyata" per bandarmologi) + lot/order untuk deteksi big money.
    """
    raw = client.order_book(code)
    book = raw[0] if isinstance(raw, list) and raw else (raw or {})

    bid_levels = _order_levels(book.get("bid"))
    offer_levels = _order_levels(book.get("offer"))

    # fallback ke shape datar lama (tanpa list level) -> 1 level sintetis.
    if not bid_levels:
        flat = _f(_pick(book, "bid1Lot", "Bid1Lot", "bidVolume"))
        if flat:
            bid_levels = [{"price": _f(_pick(book, "bid1Price", "Bid1Price")),
                           "lot": flat, "freq": _f(_pick(book, "bid1Freq", "Bid1Freq"))}]
    if not offer_levels:
        flat = _f(_pick(book, "offer1Lot", "Offer1Lot", "offerVolume"))
        if flat:
            offer_levels = [{"price": _f(_pick(book, "offer1Price", "Offer1Price")),
                             "lot": flat, "freq": _f(_pick(book, "offer1Freq", "Offer1Freq"))}]

    bid_top = bid_levels[:top_levels]
    offer_top = offer_levels[:top_levels]

    def _s(levels, key) -> float:
        return float(sum(lv[key] for lv in levels))

    def _lpo(lot, freq) -> float:
        return float(lot / freq) if freq > 0 else 0.0

    bid_top_lot, bid_top_freq = _s(bid_top, "lot"), _s(bid_top, "freq")
    offer_top_lot, offer_top_freq = _s(offer_top, "lot"), _s(offer_top, "freq")

    return {
        "bid_volume": _s(bid_levels, "lot"),        # all-level (EOD compat)
        "offer_volume": _s(offer_levels, "lot"),
        "bid_top_lot": bid_top_lot,                  # top-N (live read)
        "offer_top_lot": offer_top_lot,
        "bid_top_freq": bid_top_freq,
        "offer_top_freq": offer_top_freq,
        "bid_lot_per_order": _lpo(bid_top_lot, bid_top_freq),
        "offer_lot_per_order": _lpo(offer_top_lot, offer_top_freq),
        "n_bid_levels": float(len(bid_levels)),
        "n_offer_levels": float(len(offer_levels)),
    }
