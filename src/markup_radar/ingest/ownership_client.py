"""Struktur kepemilikan (float control) & rotasi broker per kategori.

Menjawab pertanyaan bandarmologi "barangnya siap dinaikkan atau belum":
  1. berapa % barang di tangan ritel (KSEI local_id+foreign_id) — teori:
     makin kecil porsi ritel (<15-20% dari free float), makin gampang markup;
  2. arah perpindahan barang antar bulan (ritel menyusut = akumulasi kuat,
     ritel membengkak = distribusi);
  3. net flow harian per KATEGORI broker (ritel vs asing vs smart money) —
     ritel jual + asing/smart tampung = rotasi bullish.

Sumber (shape diverifikasi live 2026-07-04, probe scratchpad):
  /analysis/shareholder/ksei/{code}?range=N
      [{code, date, price, foreign_is/cp/pf/ib/id/mf/sc/fd/ot,
        local_is/cp/pf/ib/id/mf/sc/fd/ot}]  (bulanan, nilai = LEMBAR, string)
      Jumlah SEMUA komponen 1 baris = total saham tercatat bulan itu
      (dicek WINR: sum = 5,230,116,030 = field `total` classify-table).
  /analysis/shareholder/{code}
      [{name, percentage, badge}] — badge "{PENGENDALI}", "{DIREKSI,...}".
  broker summary via broker_client.fetch_broker_summary (net_value Rupiah).

Semua angka kepemilikan = konteks BULANAN (lag KSEI), dipakai sebagai
enrichment alert/narasi — BUKAN gate classifier (keputusan Fase A: additive,
tuning bobot menyusul setelah data forward cukup — pelajaran F8).
Semua fetch fail-soft (WARN stderr, return None/{}), jangan gagalkan EOD.
"""

from __future__ import annotations

import sys

from markup_radar.ingest.broker_client import fetch_broker_summary
from markup_radar.ingest.client import InvezgoClient


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# Komponen KSEI per baris: prefix foreign_/local_ x sufiks tipe investor.
# id = individual (RITEL), cp = corporate, is = insurance, pf = pension fund,
# ib = financial institution, mf = mutual fund, sc = securities company,
# fd = foundation, ot = others.
_KSEI_SUFFIXES = ("is", "cp", "pf", "ib", "id", "mf", "sc", "fd", "ot")


def parse_ksei_rows(rows: list[dict]) -> list[dict]:
    """Baris KSEI mentah -> [{date, retail_shares, total_shares, retail_pct}]
    urut tanggal naik. Baris dgn total 0 dibuang (bulan kosong)."""
    out: list[dict] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        total = 0.0
        retail = 0.0
        for prefix in ("foreign", "local"):
            for suf in _KSEI_SUFFIXES:
                v = _f(r.get(f"{prefix}_{suf}"))
                total += v
                if suf == "id":
                    retail += v
        if total <= 0:
            continue
        date = str(r.get("date", ""))[:10]
        out.append(
            {
                "date": date,
                "retail_shares": retail,
                "total_shares": total,
                "retail_pct": round(retail / total * 100.0, 2),
            }
        )
    out.sort(key=lambda x: x["date"])
    return out


def controlling_pct(holders: list[dict]) -> float:
    """Total % pemegang ber-badge PENGENDALI (komposisi shareholder)."""
    total = 0.0
    for h in holders or []:
        if not isinstance(h, dict):
            continue
        if "PENGENDALI" in str(h.get("badge", "")).upper():
            total += _f(h.get("percentage"))
    return round(total, 2)


def summarize_ownership(
    ksei_rows: list[dict], holders: list[dict]
) -> dict | None:
    """Gabung KSEI + komposisi -> ringkasan float control.

    Return None bila KSEI kosong. `retail_float_pct` = porsi ritel dari
    free float (di luar pengendali); None bila komposisi kosong, pengendali
    >= 100%, ATAU hasil > 100% — KSEI & komposisi diambil pada tanggal beda
    (KSEI bulanan vs komposisi terkini) sehingga bisa inkonsisten; rasio
    mustahil lebih baik disembunyikan daripada menyesatkan (kasus nyata:
    WINR 105% saat probe 2026-07-04). `retail_trend_pp` = perubahan
    retail_pct dari baris KSEI tertua ke terbaru (poin persen; negatif =
    ritel menyusut = barang pindah ke tangan kuat).
    """
    series = parse_ksei_rows(ksei_rows)
    if not series:
        return None
    latest = series[-1]
    ctrl = controlling_pct(holders)
    free_float = 100.0 - ctrl
    retail_float = (
        round(latest["retail_pct"] / free_float * 100.0, 2)
        if holders and 0 < free_float <= 100
        else None
    )
    if retail_float is not None and retail_float > 100.0:
        retail_float = None
    trend_pp = round(latest["retail_pct"] - series[0]["retail_pct"], 2)
    # jarak bulan dari jumlah baris (KSEI 1 baris/bulan).
    months = max(len(series) - 1, 0)
    return {
        "asof": latest["date"],
        "retail_pct": latest["retail_pct"],          # % dari total tercatat
        "retail_float_pct": retail_float,            # % dari free float
        "controlling_pct": ctrl,
        "retail_trend_pp": trend_pp,
        "trend_months": months,
    }


def categorize_broker_flow(
    records: list[dict], categories: dict[str, list[str]]
) -> dict:
    """Net value (Rupiah) per kategori broker dari baris broker summary.

    `records`: [{broker, net_value, ...}] (hasil fetch_broker_summary).
    `categories`: {retail:[YP,..], foreign:[AK,..], smart:[AZ,..]} — mapping
    heuristik komunitas, editable di settings.yaml `broker_categories`.
    Broker di luar mapping masuk `other_net`. Return juga `n_brokers`.
    """
    lookup: dict[str, str] = {}
    for cat, codes in (categories or {}).items():
        for c in codes or []:
            lookup[str(c).upper()] = cat
    out = {"retail_net": 0.0, "foreign_net": 0.0, "smart_net": 0.0,
           "other_net": 0.0, "n_brokers": 0}
    for r in records or []:
        if not isinstance(r, dict):
            continue
        broker = str(r.get("broker", "")).upper()
        if not broker:
            continue
        net = _f(r.get("net_value"))
        cat = lookup.get(broker)
        key = f"{cat}_net" if cat in ("retail", "foreign", "smart") else "other_net"
        out[key] += net
        out["n_brokers"] += 1
    for k in ("retail_net", "foreign_net", "smart_net", "other_net"):
        out[k] = round(out[k], 2)
    return out


def fmt_rp(v: float) -> str:
    """Format Rupiah ringkas ala trader ID: T = triliun, M = miliar, jt = juta.
    Selalu bertanda (+/-) — dipakai baris rotasi di alert & narasi."""
    a = abs(v)
    if a >= 1e12:
        return f"{v / 1e12:+.1f}T"
    if a >= 1e9:
        return f"{v / 1e9:+.1f}M"
    if a >= 1e6:
        return f"{v / 1e6:+.0f}jt"
    return f"{v:+.0f}"


# ------------------------------------------------------------------ #
# Fetch (fail-soft)
# ------------------------------------------------------------------ #
def fetch_ownership(
    client: InvezgoClient, code: str, *, range_months: int = 6
) -> dict | None:
    """Ringkasan float control utk satu kode (2 call: KSEI + komposisi).

    Komposisi gagal/kosong -> tetap jalan dgn KSEI saja (retail_float_pct
    None). KSEI gagal -> None (tanpa KSEI tak ada angka inti).
    """
    try:
        ksei = client.shareholder_ksei(code, range_=range_months)
    except Exception as exc:  # noqa: BLE001 — enrichment opsional
        print(f"[WARN] ksei {code} gagal: {exc}", file=sys.stderr)
        return None
    holders: list[dict] = []
    try:
        raw = client.shareholder_composition(code)
        if isinstance(raw, list):
            holders = raw
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] shareholder {code} gagal: {exc}", file=sys.stderr)
    return summarize_ownership(ksei if isinstance(ksei, list) else [], holders)


def fetch_broker_rotation(
    client: InvezgoClient,
    code: str,
    date: str,
    categories: dict[str, list[str]],
) -> dict | None:
    """Net flow per kategori broker utk 1 hari perdagangan (1 call).

    Return None bila call gagal atau tak ada baris broker (hari libur).
    """
    try:
        df = fetch_broker_summary(client, code, date, date)
    except Exception as exc:  # noqa: BLE001 — enrichment opsional
        print(f"[WARN] broker rotation {code} gagal: {exc}", file=sys.stderr)
        return None
    if df is None or df.empty:
        return None
    flow = categorize_broker_flow(df.to_dict("records"), categories)
    return flow if flow["n_brokers"] else None
