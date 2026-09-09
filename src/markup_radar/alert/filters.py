"""Gate alert: memutuskan sinyal mana yang LAYAK DIKIRIM ke Telegram.

Dipasang 2026-09-09 setelah audit forward 405 sinyal produksi (18 Jun - 8 Sep 2026,
dibanding baseline date-matched 3.819 baris NEUTRAL dari universe yang sama).
Temuan yang memicu modul ini:

  * Apa adanya, alert KALAH dari base-rate universe-nya sendiri: win +10d 57% vs
    59%, median +1.1% vs +1.7%, excess vs IHSG -1.4% vs -0.8%.
  * Penyebabnya timing, bukan sinyalnya kosong. 42% alert terbit saat close ada di
    puncak range 20 bar (universe: cuma 12%) -- bucket dengan win 45% & excess
    -2.8%. Buang bucket itu dan alert JUSTRU MENANG atas baseline (66% vs 61%);
    di zona tengah (0.30-0.60) jadi 77% vs 68% dgn excess +1.8%.
    Filter yang sama diterapkan ke baseline cuma menaikkannya sedikit -> yang
    bekerja kombinasi sinyal engine + timing, bukan filternya sendirian.
  * Alert ke-2 dst dalam satu deretan tak menambah apa-apa: MFE median turun
    +9.5% -> +5.7%, hit +5% turun 72% -> 55%. 402 alert = cuma 196 ide unik.
  * Streak broker 0 = win 43%, excess -2.1% (16% dari alert).

PRINSIP: filter ini HANYA memotong jalur KIRIM. Classifier, confidence, levels,
penyimpanan DB dan mirror Sheets tetap memproses semua kode apa adanya, jadi
sinyal yang disaring TETAP tercatat dan bisa dievaluasi forward (kolom DB
`suppressed`). Kalau ternyata filter ini salah, buktinya ada di data sendiri.

Catatan penting: gate posisi-range & prior-run adalah gate SISI BELI. State
DISTRIBUTION_WARNING adalah peringatan jual yang secara definisi (`near_range_high`
di classifier) memang muncul di dekat puncak range -- mem-filternya dengan aturan
beli akan menghapus peringatan justru saat paling relevan. Karena itu state di
`buy_side_states` saja yang kena gate posisi; dedup episode tetap berlaku umum.
"""

from __future__ import annotations

import datetime as dt

__all__ = ["DEFAULTS", "alert_gate", "apply_alert_filters"]

DEFAULTS: dict = {
    "enabled": True,
    # Tolak sinyal beli saat close >= X dalam range 20 bar (1.0 = puncak).
    "max_range_position": 0.85,
    # Tolak sinyal beli yang sudah lari lebih dari X dalam `prior_run_window` bar.
    "max_prior_run": 0.15,
    # Tolak sinyal beli tanpa satu pun hari net-buy broker besar.
    "min_broker_streak": 1,
    # Dedup episode: alert untuk kode yang sama dalam N hari kalender terakhir
    # dianggap pengulangan ide yang sama -> tidak dikirim lagi.
    "dedup_episode": True,
    "episode_gap_days": 10,
    # State yang dianggap sinyal BELI (kena gate posisi/prior-run/streak).
    "buy_side_states": ["MARKUP_CONFIRMED", "MARKUP_START", "ACCUMULATION_ONGOING"],
}


def _cfg(filters: dict | None) -> dict:
    return {**DEFAULTS, **(filters or {})}


def alert_gate(
    record: dict,
    *,
    filters: dict | None = None,
    last_alert_date: str | None = None,
    scan_date: str | None = None,
) -> str | None:
    """Alasan sinyal ini TIDAK dikirim, atau None kalau lolos.

    `record`   : dict hasil scan (butuh `state` dan `signals`).
    `last_alert_date` : tanggal alert terakhir untuk kode ini (YYYY-MM-DD) atau
                        None kalau belum pernah. Dipakai dedup episode.
    `scan_date`: tanggal scan sekarang (YYYY-MM-DD).

    Alasan dikembalikan sebagai string pendek yang stabil (dipakai di log,
    kolom DB `suppressed`, dan agregasi evaluasi), bukan kalimat bebas.
    """
    f = _cfg(filters)
    if not f.get("enabled", True):
        return None

    state = record.get("state", "")
    sig = record.get("signals") or {}

    # --- dedup episode (berlaku untuk semua state) ---
    if f.get("dedup_episode", True) and last_alert_date and scan_date:
        gap_days = int(f.get("episode_gap_days", 10))
        try:
            gap = (dt.date.fromisoformat(scan_date)
                   - dt.date.fromisoformat(str(last_alert_date)[:10])).days
        except ValueError:
            gap = None
        if gap is not None and 0 <= gap <= gap_days:
            return f"episode_ulang({gap}h)"

    # --- gate sisi beli ---
    if state not in set(f.get("buy_side_states", [])):
        return None

    max_pos = f.get("max_range_position")
    if max_pos is not None:
        pos = sig.get("range_position")
        if pos is not None and float(pos) > float(max_pos):
            return f"puncak_range({float(pos):.2f})"

    max_run = f.get("max_prior_run")
    if max_run is not None:
        run = sig.get("prior_run")
        if run is not None and float(run) > float(max_run):
            return f"sudah_lari({float(run):+.0%})"

    min_streak = f.get("min_broker_streak")
    if min_streak is not None:
        streak = sig.get("broker_net_buy_streak")
        if streak is not None and int(streak) < int(min_streak):
            return f"streak_broker({int(streak)})"

    return None


def apply_alert_filters(
    records: list[dict],
    *,
    filters: dict | None = None,
    last_alert_dates: dict[str, str] | None = None,
    scan_date: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Pisahkan record actionable jadi (dikirim, ditahan).

    Menulis `record["suppressed"]` = alasan (str) pada yang ditahan, dan None
    pada yang lolos — supaya alasannya ikut tersimpan ke DB/Sheets dan bisa
    dievaluasi forward (apakah yang kita buang ternyata naik?).
    """
    last = last_alert_dates or {}
    keep: list[dict] = []
    drop: list[dict] = []
    for r in records:
        reason = alert_gate(
            r,
            filters=filters,
            last_alert_date=last.get(r.get("code", "")),
            scan_date=scan_date,
        )
        r["suppressed"] = reason
        (drop if reason else keep).append(r)
    return keep, drop
