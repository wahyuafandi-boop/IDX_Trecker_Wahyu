"""Normalisasi broker summary (S3 net flow, S4 concentration) & order book (S5)."""

from __future__ import annotations

import pandas as pd

from markup_radar.ingest.client import InvezgoClient


def _pick(row: dict, *keys: str, default=None):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def fetch_broker_summary(
    client: InvezgoClient, code: str, date_from: str, date_to: str
) -> pd.DataFrame:
    """Broker summary -> DataFrame[broker, buy_value, sell_value, net_value, net_volume].

    Satu baris per broker (akumulasi rentang date_from..date_to).
    """
    raw = client.broker_summary_stock(code, date_from, date_to)
    rows = raw if isinstance(raw, list) else raw.get("brokers", raw.get("items", []))

    records = []
    for r in rows:
        buy_val = _pick(r, "buyValue", "BuyValue", "buy_value", default=0) or 0
        sell_val = _pick(r, "sellValue", "SellValue", "sell_value", default=0) or 0
        records.append(
            {
                "broker": _pick(r, "broker", "code", "brokerCode", default="?"),
                "buy_value": buy_val,
                "sell_value": sell_val,
                "net_value": _pick(r, "netValue", "NetValue", default=buy_val - sell_val),
                "net_volume": _pick(r, "netVolume", "NetVolume", default=0),
            }
        )
    return pd.DataFrame.from_records(records)


def fetch_broker_daily_net(
    client: InvezgoClient, code: str, date_from: str, date_to: str
) -> list[float]:
    """Net broker besar per hari (kronologis) untuk S3 streak — 1 API call.

    Pakai inventory-chart (time-series akumulasi/distribusi) alih-alih loop
    broker-summary per tanggal. Tiap titik di-reduksi jadi satu skalar net
    harian (positif = akumulasi). Return [] bila data tak tersedia (streak=0).

    TODO(verify): shape inventory-chart Invezgo — sesuaikan field di bawah.
    """
    try:
        raw = client.inventory_chart_stock(code, date_from, date_to)
    except Exception:  # noqa: BLE001 — streak opsional, jangan gagalkan saham
        return []

    rows = raw if isinstance(raw, list) else raw.get("items", raw.get("data", []))
    series: list[tuple] = []
    for r in rows:
        date = _pick(r, "date", "time", "timestamp")
        net = _pick(r, "net", "netValue", "NetValue", "value", default=None)
        if net is None:
            buy = _pick(r, "buy", "buyValue", "Buy", default=0) or 0
            sell = _pick(r, "sell", "sellValue", "Sell", default=0) or 0
            net = buy - sell
        series.append((date, float(net)))

    series.sort(key=lambda x: x[0] or "")
    return [net for _, net in series]


def fetch_closing_queue(client: InvezgoClient, code: str) -> dict[str, float]:
    """Order book -> {bid_volume, offer_volume} di level teratas (S5)."""
    raw = client.order_book(code)
    book = raw[0] if isinstance(raw, list) and raw else raw
    book = book or {}
    return {
        "bid_volume": _pick(book, "bid1Lot", "Bid1Lot", "bidVolume", default=0) or 0,
        "offer_volume": _pick(book, "offer1Lot", "Offer1Lot", "offerVolume", default=0) or 0,
    }
