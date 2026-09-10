"""Insider (laporan kepemilikan) & kalender corporate action per emiten.

Sumber (path terkonfirmasi dari invezgo-go-sdk, shape diverifikasi live via
scripts/verify_insider.py, 2026-07-04):

  /analysis/shareholder-insider  (market-wide, paged)
    [{date, code, name, prev_percent, next_percent, change, badge, purpose,
      subrow:[{date, price, status: Buy|Sell|Other, value}]}]
    `change` = perubahan kepemilikan dalam POIN PERSEN (next - prev);
    negatif = jual/divestasi. `date` = timestamp laporan (ISO, suffix Z).

  /analysis/calendar?code=...
    [{code, type, payload:{...}}] — payload beda-beda per `type`
    (RUPS_SCHEDULE: Date/Remark/RecDate; PUBLIC_EXPOSE: Datetime/DateStr;
    CONVERTION: TradeDate/RecDate; dst). Bisa berisi event lampau, jadi
    difilter window [on_date, on_date + horizon] di sini.

Desain hemat kuota: insider ditarik SEKALI market-wide lalu di-intersect
lokal dengan watchlist; calendar per-kode HANYA untuk kode actionable
(di-cap caller). Semua fungsi fetch fail-soft (return kosong, jangan
gagalkan run EOD).
"""

from __future__ import annotations

import datetime as dt
import sys
from typing import Any

from markup_radar.ingest.client import InvezgoClient


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _iso_date(value: Any) -> str:
    """Ambil 'YYYY-MM-DD' dari string tanggal Invezgo (ISO dgn/atau tanpa Z,
    atau DateStr 'YYYYMMDD'). Return '' bila tak bisa diparse."""
    s = str(value or "").strip()
    if not s:
        return ""
    if len(s) == 8 and s.isdigit():  # DateStr PUBLIC_EXPOSE: '20260625'
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    try:
        return dt.date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return ""


# ------------------------------------------------------------------ #
# Insider
# ------------------------------------------------------------------ #
def summarize_insider_rows(
    rows: list[dict], codes: set[str] | None = None
) -> dict[str, dict]:
    """Agregasi baris insider market-wide -> ringkasan per kode.

    Return {code: {n_reports, net_change_pct, buys, sells, last_date,
    last_name, last_purpose, last_change_pct}}. `net_change_pct` = jumlah
    `change` (poin persen kepemilikan; + = akumulasi insider, - = divestasi).
    `codes` None = semua kode dipertahankan. Baris terbaru diasumsikan lebih
    dulu (urutan API), tapi last_* tetap dipilih via max(date) agar aman.
    """
    out: dict[str, dict] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = str(r.get("code", "")).upper()
        if not code or (codes is not None and code not in codes):
            continue
        change = _f(r.get("change"))
        s = out.setdefault(
            code,
            {
                "n_reports": 0, "net_change_pct": 0.0, "buys": 0, "sells": 0,
                "last_date": "", "last_name": "", "last_purpose": "",
                "last_change_pct": 0.0,
            },
        )
        s["n_reports"] += 1
        s["net_change_pct"] += change
        if change > 0:
            s["buys"] += 1
        elif change < 0:
            s["sells"] += 1
        date = _iso_date(r.get("date"))
        if date >= s["last_date"]:
            s["last_date"] = date
            s["last_name"] = str(r.get("name", "")).strip()
            s["last_purpose"] = str(r.get("purpose", "")).strip()
            s["last_change_pct"] = change
    for s in out.values():
        s["net_change_pct"] = round(s["net_change_pct"], 4)
    return out


def fetch_insider_map(
    client: InvezgoClient,
    date_from: str,
    date_to: str,
    codes: set[str] | None = None,
    *,
    limit: int = 50,
    max_pages: int = 6,
) -> dict[str, dict]:
    """Tarik insider market-wide (paged) lalu ringkas per kode watchlist.

    Maks `max_pages` call utk SEMUA saham (bukan per kode). `limit` server
    maks ~50 — limit=100 balik HTTP 500 (diverifikasi live 2026-07-04).
    Fail-soft: error API -> lanjut dgn baris yang sudah terkumpul (WARN ke
    stderr), fitur opsional jangan gagalkan scan EOD.
    """
    rows: list[dict] = []
    try:
        for page in range(1, max_pages + 1):
            batch = client.shareholder_insider(
                date_from, date_to, page=page, limit=limit
            )
            if not isinstance(batch, list) or not batch:
                break
            rows.extend(batch)
            if len(batch) < limit:
                break
    except Exception as exc:  # noqa: BLE001 — enrichment opsional
        print(f"[WARN] insider fetch gagal (pakai {len(rows)} baris terkumpul): "
              f"{exc}", file=sys.stderr)
    return summarize_insider_rows(rows, codes)


# ------------------------------------------------------------------ #
# Corporate action calendar
# ------------------------------------------------------------------ #
# Label pendek utk alert Telegram; type tak dikenal -> Title Case apa adanya.
_TYPE_LABEL = {
    "RUPS_SCHEDULE": "RUPS",
    "PUBLIC_EXPOSE": "PubEx",
    "CASH_DIVIDEND": "Dividen",
    "STOCK_SPLIT": "Split",
    "REVERSE_SPLIT": "Reverse Split",
    "RIGHT_ISSUE": "Rights",
    "CONVERTION": "Konversi",
    "IPO": "IPO",
    "TENDER_OFFER": "Tender Offer",
}

# Kandidat field tanggal utama di payload, dicoba berurutan.
_DATE_KEYS = ("Date", "Datetime", "TradeDate", "CumDate", "RecDate", "DateStr")


def parse_calendar_items(
    items: list[dict], on_date: str, *, horizon_days: int = 21
) -> list[dict]:
    """Normalisasi item calendar -> [{type, label, date, remark}] dalam window
    [on_date, on_date + horizon_days], urut tanggal naik.

    Event tanpa tanggal parseable atau di luar window dibuang (API bisa
    balikin event lampau).
    """
    try:
        start = dt.date.fromisoformat(on_date)
    except ValueError:
        return []
    end = start + dt.timedelta(days=horizon_days)

    out: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        payload = it.get("payload") or {}
        date = ""
        for key in _DATE_KEYS:
            date = _iso_date(payload.get(key))
            if date:
                break
        if not date:
            continue
        d = dt.date.fromisoformat(date)
        if not (start <= d <= end):
            continue
        type_ = str(it.get("type", "")).upper()
        label = _TYPE_LABEL.get(type_, type_.replace("_", " ").title())
        remark = str(payload.get("Remark", "") or "").strip()
        if remark and remark != "-":
            label = f"{label} {remark}"  # mis. 'RUPS AGM' / 'RUPS EGM'
        out.append({"type": type_, "label": label, "date": date, "remark": remark})
    out.sort(key=lambda x: x["date"])
    return out


def fetch_upcoming_actions(
    client: InvezgoClient,
    code: str,
    on_date: str,
    *,
    horizon_days: int = 21,
    limit: int = 12,
) -> list[dict]:
    """Corporate action mendatang utk satu kode (1 call). Fail-soft -> []."""
    try:
        raw = client.calendar(code=code, limit=limit)
    except Exception as exc:  # noqa: BLE001 — enrichment opsional
        print(f"[WARN] calendar {code} gagal: {exc}", file=sys.stderr)
        return []
    items = raw if isinstance(raw, list) else []
    return parse_calendar_items(items, on_date, horizon_days=horizon_days)
