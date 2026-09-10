#!/usr/bin/env python3
"""Entrypoint EOD: scan watchlist -> sinyal -> klasifikasi -> store -> alert.

Contoh:
    python scripts/run_daily.py                 # tanggal hari ini
    python scripts/run_daily.py --date 2026-06-16
    python scripts/run_daily.py --dry-run       # tanpa kirim Telegram
    python scripts/run_daily.py --codes BBCA,BBRI   # override watchlist YAML

Catatan: butuh INVEZGO_API_KEY di .env. Beberapa path endpoint Invezgo masih
perlu diverifikasi (lihat scripts/verify_data.py & client.py TODO).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Izinkan import 'markup_radar' tanpa install (src layout).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.alert import (
    apply_alert_filters,
    format_alert,
    format_batch_header,
    format_signal,
    send_telegram,
)
from markup_radar.config import load_codes_file, load_settings, parse_codes
from markup_radar.ingest import InvezgoClient
from markup_radar.ingest.broker_client import (
    fetch_broker_daily_net_dated,
    fetch_closing_queue,
)
from markup_radar.ingest.done_client import (
    fetch_done_breakdown,
    latest_available_done_date,
)
from markup_radar.ingest.ihsg_client import fetch_ihsg
from markup_radar.ingest.insider_client import (
    fetch_insider_map,
    fetch_upcoming_actions,
)
from markup_radar.ingest.ohlc_client import fetch_ohlcv
from markup_radar.ingest.ownership_client import (
    fetch_broker_rotation,
    fetch_ownership,
    fmt_rp,
)
from markup_radar.narrative import generate_narrative
from markup_radar.scoring import classify, confidence_markup_start, rank_alerts
from markup_radar.signals import StockData, compute_signals
from markup_radar.signals.levels import bow_zone, compute_trade_levels
from markup_radar.signals.market import market_regime
from markup_radar.store import Store, build_sink


def _date_range(end: dt.date, days_back: int) -> tuple[str, str]:
    start = end - dt.timedelta(days=days_back)
    return start.isoformat(), end.isoformat()


def _atomic_write(out_path: Path, content: str) -> None:
    """Tulis file atomik (tmp + rename) — tak ada file separuh saat crash."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out_path.parent), prefix=".live_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# Prioritas slot live-watch: setup entry (MARKUP_*) dulu, baru pantau BOW.
_LIVE_TIER = {"MARKUP_CONFIRMED": 0, "MARKUP_START": 1, "ACCUMULATION_ONGOING": 2}

# Minimum bar OHLCV supaya RVOL (MA20), donchian (20) & range_position berarti.
MIN_BARS = 21

# Ambang "scan ini gagal, bukan pasar yang sepi": porsi kode yang datanya tak
# bisa ditarik. Audit 2026-09-09 menemukan 5 malam (21-22 Jul, 21/24/28 Agu) di
# mana token Invezgo balas 401 untuk SEMUA kode; run tetap selesai dan mengirim
# "Tidak ada sinyal actionable hari ini" — tak bisa dibedakan dari malam sepi
# yang normal. Di atas ambang ini, run melapor sebagai GANGGUAN.
SCAN_FAILURE_ALERT_RATIO = 0.30


def _write_live_codes(actionable: list[dict], cfg, *, dry_run: bool) -> list[str]:
    """Pilih subset kode untuk live-watch besok pagi dari hasil scan EOD.

    Ambil setup sesuai `live_watch.include_states` — MARKUP (CONFIRMED + START,
    kandidat entry yang perlu konfirmasi order book live) dan opsional
    ACCUMULATION_ONGOING (pantau zona BOW). Urut tier state (MARKUP dulu) lalu
    confidence, cap `max_codes`. Tulis atomik ke `live_today.txt` supaya
    run_live.sh polling FOKUS ke setup terbaik, bukan seluruh universe 50 kode
    (hemat ~80% kuota live tanpa kehilangan nilai trading).

    Sidecar `live_levels.json` ({code: {state, support, bow_lo, bow_hi, entry}})
    ikut ditulis utk kode terpilih yang punya levels — dibaca live_watch buat
    monitor BOW-AC (harga masuk zona + bid dijaga) & invalidasi (jebol support).

    Kedua file SELALU ditulis (mencerminkan setup malam ini): nol setup -> txt
    hanya header + json `{}` -> run_live besok exit cepat (0 call). Dilewati
    saat dry-run agar file produksi tak terkotori data uji.
    """
    lw = cfg.live_watch
    states = set(lw.get("include_states", ["MARKUP_CONFIRMED", "MARKUP_START"]))
    max_codes = int(lw.get("max_codes", 5))
    out_path = Path(lw.get("out_file", "live_today.txt"))
    levels_path = Path(lw.get("levels_file", "live_levels.json"))

    picks = [r for r in actionable if r.get("state") in states]
    # Slot live dipilih by PERINGKAT ENTRY dulu (rank_alerts), baru tier state.
    # `confidence` cuma tie-break terakhir — skor itu terbukti anti-prediktif
    # (AUC 0.439), memakainya sebagai kunci utama memboroskan slot live.
    picks.sort(key=lambda r: (r.get("rank") or 999,
                              _LIVE_TIER.get(r.get("state"), 9),
                              -r.get("confidence", 0)))
    picks = picks[:max_codes]
    codes = [r["code"] for r in picks]

    print(f"[info] live-watch besok ({len(codes)}/{max_codes} kode): "
          f"{', '.join(codes) or '(kosong)'}", file=sys.stderr)
    if dry_run:
        print("[info] dry-run: live_today.txt & live_levels.json tidak ditulis.",
              file=sys.stderr)
        return codes

    header = (
        f"# auto-generated oleh run_daily.py @ "
        f"{dt.datetime.now().isoformat(timespec='seconds')}\n"
        f"# {len(codes)} kode live-watch (top {max_codes} by tier+confidence "
        f"dari {len(actionable)} actionable)\n"
    )
    body = ("\n".join(codes) + "\n") if codes else ""
    _atomic_write(out_path, header + body)

    levels_map: dict[str, dict] = {}
    for r in picks:
        lv = r.get("levels")
        if not lv:
            continue
        bow_lo, bow_hi = bow_zone(lv)
        levels_map[r["code"]] = {
            "state": r.get("state", ""),
            "support": lv.get("support"),
            "bow_lo": bow_lo,
            "bow_hi": bow_hi,
            "entry": lv.get("entry"),
            "resistance": lv.get("resistance"),
        }
    _atomic_write(levels_path, json.dumps(levels_map, indent=1))
    print(f"[OK] {len(codes)} kode live -> {out_path}; "
          f"{len(levels_map)} level -> {levels_path}", file=sys.stderr)
    return codes


def _enrich_actionable(
    client: InvezgoClient, actionable: list[dict], date: dt.date, cfg
) -> None:
    """Tempelkan konteks insider + corporate action + float control ke
    record actionable.

    Hemat kuota: insider = 1-3 call MARKET-WIDE (intersect lokal dgn kode
    actionable, bukan per saham); calendar 1 call & ownership 3 call per
    kode actionable, masing-masing di-cap (max_calendar_codes / max_codes)
    dgn prioritas state MARKUP (tier entry yang paling butuh konteks).
    Fail-soft: modul insider/ownership sudah menelan error API -> record
    tanpa key terkait, alert tetap jalan. Fase A: enrichment SAJA — tidak
    menyentuh classifier/confidence (pelajaran F8: tuning bobot menyusul
    setelah data forward cukup).
    """
    if not actionable:
        return
    # Prioritas cap kuota: pakai PERINGKAT ENTRY (rank_alerts) kalau sudah ada.
    # Dulu memakai `confidence`, padahal skor itu terbukti TERBALIK (AUC 0.439)
    # — akibatnya kuota enrichment justru terpakai pada kandidat terburuk.
    prio = {"MARKUP_CONFIRMED": 0, "MARKUP_START": 1}
    ranked = sorted(
        actionable,
        key=lambda r: (r.get("rank") or 999,
                       prio.get(r["state"], 9),
                       -r.get("confidence", 0)),
    )

    ins_cfg = cfg.insider
    if ins_cfg.get("enabled"):
        lookback = int(ins_cfg.get("lookback_days", 30))
        horizon = int(ins_cfg.get("calendar_horizon_days", 21))
        max_cal = int(ins_cfg.get("max_calendar_codes", 10))

        codes = {r["code"] for r in actionable}
        ins_map = fetch_insider_map(client, *_date_range(date, lookback), codes)
        for r in actionable:
            info = ins_map.get(r["code"])
            if info:
                r["insider"] = {**info, "window_days": lookback}

        for r in ranked[:max_cal]:
            acts = fetch_upcoming_actions(
                client, r["code"], date.isoformat(), horizon_days=horizon
            )
            if acts:
                r["corp_actions"] = acts

    own_cfg = cfg.ownership
    if own_cfg.get("enabled"):
        range_months = int(own_cfg.get("ksei_range_months", 6))
        max_own = int(own_cfg.get("max_codes", 10))
        cats = cfg.broker_categories
        for r in ranked[:max_own]:
            own = fetch_ownership(client, r["code"], range_months=range_months)
            if own:
                r["ownership"] = own
            rot = fetch_broker_rotation(client, r["code"], date.isoformat(), cats)
            if rot:
                r["rotation"] = rot

    n_ins = sum(1 for r in actionable if r.get("insider"))
    n_ca = sum(1 for r in actionable if r.get("corp_actions"))
    n_own = sum(1 for r in actionable if r.get("ownership"))
    n_rot = sum(1 for r in actionable if r.get("rotation"))
    print(f"[info] enrichment: insider {n_ins}, corp-action {n_ca}, "
          f"ownership {n_own}, rotasi {n_rot} dari {len(actionable)} kode.",
          file=sys.stderr)


def _extra_context(record: dict) -> str:
    """Ringkas insider/corp action jadi kalimat pendek utk prompt narasi."""
    parts: list[str] = []
    ins = record.get("insider")
    if ins:
        parts.append(
            f"insider {ins.get('window_days', 30)} hari: net "
            f"{ins['net_change_pct']:+.2f} poin persen kepemilikan "
            f"({ins['n_reports']} laporan, terakhir "
            f"{ins.get('last_purpose') or 'n/a'} {ins.get('last_date', '')})"
        )
    ca = record.get("corp_actions")
    if ca:
        parts.append(
            "corporate action: "
            + ", ".join(f"{c['label']} {c['date']}" for c in ca[:3])
        )
    own = record.get("ownership")
    if own:
        seg = f"float control: ritel pegang {own['retail_pct']:.1f}% saham tercatat"
        if own.get("retail_float_pct") is not None:
            seg += (f" (~{own['retail_float_pct']:.0f}% dari free float; "
                    f"pengendali {own['controlling_pct']:.1f}%)")
        if own.get("trend_months"):
            pp = own.get("retail_trend_pp", 0.0)
            makna = ("ritel menyusut = barang pindah ke tangan kuat" if pp < 0
                     else "ritel membengkak = indikasi distribusi" if pp > 0
                     else "stabil")
            seg += (f", tren ritel {pp:+.1f} pp dalam "
                    f"{own['trend_months']} bulan ({makna})")
        parts.append(seg)
    rot = record.get("rotation")
    if rot:
        parts.append(
            f"rotasi broker hari ini: ritel {fmt_rp(rot['retail_net'])}, "
            f"asing {fmt_rp(rot['foreign_net'])}, "
            f"smart money {fmt_rp(rot['smart_net'])} "
            f"(ritel jual + asing/smart tampung = rotasi bullish)"
        )
    return "; ".join(parts)


def build_stock_data(
    client: InvezgoClient,
    code: str,
    date: dt.date,
    cfg,
    *,
    ihsg_close,
) -> StockData:
    """Tarik & normalisasi data EOD per saham (4 call/saham).

    IHSG sudah ditarik 1x per run dan di-inject ke sini.

    S4 broker_concentration & S8 foreign TIDAK di-fetch: keduanya tidak dipakai
    classify()/confidence (plumbed-but-unused, audit 2026-06-21), jadi
    fetch_broker_summary (dulu call #5) & top/foreign di-skip demi hemat kuota.
    Field StockData terkait default (concentration/foreign_net = 0). Bila S4/S8
    nanti di-wire ke scoring, tambah lagi fetch-nya di sini.
    """
    windows = cfg.windows
    ohlc_from, ohlc_to = _date_range(date, max(windows.get("volume_ma", 20) * 2, 60))
    streak_lb = windows.get("broker_streak_lookback", 5)
    comp_lb = windows.get("compatibility_lookback", 45)

    ohlcv = fetch_ohlcv(client, code, ohlc_from, ohlc_to)              # 1
    done = fetch_done_breakdown(client, code, date.isoformat())       # 2
    queue = fetch_closing_queue(client, code)                         # 3
    # S3 streak + S11 compatibility dari 1 call inventory-chart yang sama —
    # range diperpanjang utk korelasi; streak tetap dipotong ke window lama
    # (streak_lb) agar nilainya tak berubah vs histori sinyal.
    dated_net = fetch_broker_daily_net_dated(
        client, code, *_date_range(date, max(streak_lb, comp_lb)))    # 4
    streak_cut = (date - dt.timedelta(days=streak_lb)).isoformat()
    daily_net = [n for d, n in dated_net if str(d)[:10] >= streak_cut]

    return StockData(
        code=code,
        ohlcv=ohlcv,
        done_offer_value=done["done_offer_value"],
        done_bid_value=done["done_bid_value"],
        broker_daily_net=daily_net,
        broker_daily_net_dated=dated_net,
        closing_bid_volume=queue["bid_volume"],
        closing_offer_volume=queue["offer_volume"],
        ihsg_close=ihsg_close,
    )


def evaluate(data: StockData, signals: dict, cfg, eff: dict):
    """Klasifikasi + confidence + trade levels untuk satu saham (pure, tanpa network).

    `eff` = thresholds dasar + overlay profil regime (di-resolve sekali per run di
    main()). Trade levels dihitung untuk MARKUP_* (rencana trade, spec D5) DAN
    ACCUMULATION_ONGOING (panduan level pantau: entry bersyarat breakout/BOB,
    area BOW, batas invalidasi — bukan sinyal masuk). DISTRIBUTION/NEUTRAL ->
    None. atr_mult_sl & rr_target diambil dari profil (`eff`), sisanya dari
    blok `levels` config.
    """
    state = classify(signals, eff)
    conf = confidence_markup_start(signals, cfg.score_weights)
    levels = None
    if state in ("MARKUP_START", "MARKUP_CONFIRMED", "ACCUMULATION_ONGOING"):
        lv = cfg.levels
        levels = compute_trade_levels(
            data.ohlcv,
            lookback=cfg.windows.get("donchian_lookback", 20),
            atr_period=lv.get("atr_period", 14),
            breakout_buffer=lv.get("breakout_buffer", 0.005),
            atr_mult_sl=eff.get("atr_mult_sl", 2.0),
            rr_target=eff.get("rr_target", 2.0),
            min_stop_pct=lv.get("min_stop_pct", 0.03),
            hold_slack=lv.get("hold_slack", 1.8),
        )
    return state, conf, levels


def main() -> int:
    ap = argparse.ArgumentParser(description="Markup Radar — daily EOD scan")
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="jangan kirim Telegram")
    ap.add_argument(
        "--codes",
        nargs="+",
        metavar="CODE",
        help="override watchlist untuk run ini, mis. --codes BBCA,BBRI atau "
        "--codes BBCA BBRI. Kalau tidak diberikan, pakai watchlist di "
        "config/settings.yaml.",
    )
    ap.add_argument(
        "--codes-file",
        metavar="PATH",
        help="baca watchlist dari file teks (1 kode/baris, hasil screening "
        "Stockbit), mis. --codes-file watchlist_today.txt. Dikalahkan oleh --codes.",
    )
    args = ap.parse_args()

    cfg = load_settings()
    override = None
    if args.codes:
        override = parse_codes(args.codes)
        src = "--codes"
    elif args.codes_file:
        try:
            override = load_codes_file(args.codes_file)
        except OSError as exc:
            print(f"[ERROR] gagal baca --codes-file: {exc}", file=sys.stderr)
            return 1
        src = args.codes_file
    if override is not None:
        if override:
            cfg.raw["watchlist"] = override
            print(f"[info] watchlist override ({src}): {', '.join(override)}",
                  file=sys.stderr)
        else:
            print(f"[WARN] {src} tidak berisi kode valid; "
                  "fallback ke watchlist config.", file=sys.stderr)
    client = InvezgoClient(
        cfg.invezgo_api_key,
        cfg.invezgo_base_url,
        rate_limit_per_min=cfg.rate_limit_per_min,
    )

    # Resolusi tanggal scan: done (momentum-chart) telat ~1 hari bursa & kosong di
    # libur/weekend. Pakai tanggal terakhir yang sudah ada data done agar MARKUP
    # bisa terhitung (kalau hari ini belum siap, mundur). Aman utk swing 20-hari.
    scan_date_str = args.date
    if cfg.watchlist:
        resolved = latest_available_done_date(client, cfg.watchlist[0], args.date)
        if resolved != args.date:
            print(f"[info] data done belum tersedia utk {args.date}; "
                  f"pakai tanggal terakhir berdata: {resolved}", file=sys.stderr)
            scan_date_str = resolved
    date = dt.date.fromisoformat(scan_date_str)

    store = Store(cfg.db_path)
    narrative_cfg = cfg.narrative
    # Mirror persisten ke Google Sheets (histori numpuk lintas run GH Actions).
    # None bila sink mati (fitur off / tak ada spreadsheet_id / kredensial / lib).
    sink = build_sink(cfg)
    if sink is not None:
        print("[info] mirror Google Sheets aktif.", file=sys.stderr)

    # Peringatan operasional (bukan sinyal trading) yang dikirim terpisah ke
    # Telegram di akhir run: provider narasi mati, scan gagal massal, IHSG kosong.
    # Ada karena kegagalan senyap terbukti berlangsung berminggu-minggu tanpa
    # terdeteksi — log VPS saja tidak cukup, harus sampai ke HP.
    ops_alerts: list[str] = []

    # Data market-wide: IHSG ditarik 1x per run, dipakai semua saham (hemat kuota).
    ihsg = fetch_ihsg(client, *_date_range(date, cfg.windows.get("ihsg_ma", 50) * 2))
    ihsg_close = ihsg["close"] if not ihsg.empty else None
    ihsg_close_ok = ihsg_close is not None and not ihsg.empty

    # Regime selector (spec §4.6): IHSG vs MA -> profil parameter, di-resolve SEKALI
    # per run. eff = thresholds dasar + overlay profil regime (rvol/RS-gate/SL/RR).
    # Fail-safe: IHSG kosong -> market_regime balik BEARISH (profil lebih ketat).
    regime = market_regime(ihsg_close, cfg.windows.get("ihsg_ma", 50))
    eff = {**cfg.thresholds, **cfg.regime_profiles.get(regime.value, {})}
    print(f"[info] regime: {regime.value} "
          f"(rvol_spike={eff.get('rvol_spike')}, "
          f"require_rs={eff.get('require_relative_strength', False)})", file=sys.stderr)

    scan_log: list[dict] = []   # SEMUA kode (termasuk NEUTRAL) untuk mirror Sheets
    candidates: list[dict] = []  # lolos alert_states, BELUM lewat gate alert
    failed: list[str] = []       # kode yang datanya gagal ditarik / dihitung
    thin: list[str] = []         # kode dgn histori terlalu pendek (mis. pasca-split)
    for code in cfg.watchlist:
        try:
            data = build_stock_data(
                client, code, date, cfg, ihsg_close=ihsg_close,
            )
            # Histori terlalu pendek -> RVOL/MA/donchian dihitung di atas
            # beberapa bar saja dan menghasilkan angka yang kelihatan valid tapi
            # tidak berarti. Penyebab paling umum: deret dipotong di aksi
            # korporasi (lihat ohlc_client.trim_at_corporate_action) atau saham
            # baru IPO. Lebih baik DILEWATI daripada memancarkan sinyal palsu —
            # MLPT pasca-split 1:20 sempat memicu MARKUP_START dgn RVOL 341x.
            if len(data.ohlcv) < MIN_BARS:
                thin.append(code)
                print(f"[WARN] {code}: histori cuma {len(data.ohlcv)} bar "
                      f"(< {MIN_BARS}) — dilewati, sinyal tak bisa dipercaya.",
                      file=sys.stderr)
                continue
            signals = compute_signals(data, cfg.thresholds, cfg.windows, cfg.broker_top_n)
            state, conf, levels = evaluate(data, signals, cfg, eff)
        except Exception as exc:  # noqa: BLE001 — jangan gagalkan seluruh batch
            failed.append(code)
            print(f"[WARN] {code}: {exc}", file=sys.stderr)
            continue

        rs = signals.get("relative_strength", 0.0)
        levels_dict = levels.as_dict() if levels else None
        store.save_result(
            scan_date_str, code, state, conf, signals,
            regime=regime.value, relative_strength=round(rs, 4),
            levels=levels_dict,
        )
        print(f"{code:6s} {state:22s} conf={conf} RS={rs:+.2%}")

        record = {
            "date": scan_date_str, "code": code, "state": state,
            "confidence": conf, "signals": signals, "narrative": "",
            "regime": regime.value,
            "relative_strength": round(rs, 4),
            "levels": levels_dict,
            "alert_sent": False,   # di-set True setelah send_telegram sukses
            "suppressed": None,    # di-set gate alert (alert/filters.py)
        }
        if state in cfg.alert_states:
            candidates.append(record)
        scan_log.append(record)

    # --- Kesehatan scan: gangguan atau memang pasar sepi? --------------------
    n_total = len(cfg.watchlist)
    n_bad = len(failed) + len(thin)
    if n_total and n_bad / n_total >= SCAN_FAILURE_ALERT_RATIO:
        print(f"[ERROR] SCAN GAGAL SEBAGIAN BESAR: {len(failed)} error + "
              f"{len(thin)} histori tipis dari {n_total} kode "
              f"({n_bad / n_total:.0%}). Hasil malam ini TIDAK bisa dipercaya.",
              file=sys.stderr)
        ops_alerts.append(
            f"Scan bermasalah: {n_bad}/{n_total} kode gagal "
            f"({n_bad / n_total:.0%}). Cek token Invezgo (401), kuota (429), "
            f"dan konektivitas VPS. Sinyal malam ini tidak bisa dipercaya."
        )
    elif failed:
        print(f"[info] {len(failed)}/{n_total} kode gagal ditarik: "
              f"{', '.join(failed[:12])}{' ...' if len(failed) > 12 else ''}",
              file=sys.stderr)
    if not ihsg_close_ok:
        ops_alerts.append(
            "IHSG (COMPOSITE) gagal ditarik — regime jatuh ke fail-safe BEARISH "
            "dan relative strength tak terhitung. Sinyal malam ini lebih ketat "
            "dari seharusnya."
        )

    # --- Gate alert (blok `alert_filters`) -----------------------------------
    # Memotong jalur KIRIM saja: semua kode di atas SUDAH tersimpan ke DB apa
    # adanya, jadi sinyal yang ditahan tetap bisa dievaluasi forward lewat kolom
    # `suppressed`. Dasar empiris ada di settings.yaml & alert/filters.py.
    # Ditempatkan SEBELUM enrichment/narasi -> yang ditahan tak membakar kuota
    # Invezgo (3-4 call/kode) maupun panggilan LLM.
    last_alerts = store.last_alert_dates(
        [r["code"] for r in candidates], scan_date_str,
        within_days=int(cfg.alert_filters.get("episode_gap_days", 10)) * 3,
    )
    actionable, held = apply_alert_filters(
        candidates,
        filters=cfg.alert_filters,
        last_alert_dates=last_alerts,
        scan_date=scan_date_str,
    )
    if candidates:
        print(f"[info] gate alert: {len(actionable)}/{len(candidates)} lolos, "
              f"{len(held)} ditahan.", file=sys.stderr)
    if held:
        # Sejajar dgn mark_alert_sent: hanya run SUNGGUHAN yang menyentuh kolom
        # audit. Dry-run pada tanggal lampau kalau tidak dijaga akan menimpa
        # catatan "pernah dikirim" dengan alasan penahanan dan merusak bahan
        # evaluasi forward.
        if not args.dry_run:
            store.mark_suppressed(
                scan_date_str, {r["code"]: r["suppressed"] for r in held}
            )
        by_reason: dict[str, list[str]] = {}
        for r in held:
            by_reason.setdefault(r["suppressed"].split("(")[0], []).append(r["code"])
        for reason, codes in sorted(by_reason.items()):
            print(f"         - {reason}: {', '.join(sorted(codes))}", file=sys.stderr)

    # Urutkan berdasarkan PELUANG ENTRY (scoring/probability.py) — user tak
    # mungkin masuk ke 8-10 sinyal semalam, jadi yang terpenting adalah "mana
    # dulu". Ditaruh sebelum enrichment supaya kuota insider/ownership (cap 10
    # kode) terpakai pada kandidat terbaik, bukan yang kebetulan di urutan atas.
    # CATATAN: ini BUKAN `confidence` — skor itu diuji terbalik (AUC 0.439).
    actionable = rank_alerts(actionable)
    top = [r for r in actionable if r.get("score_band") == "TINGGI"]
    print(f"[info] peringkat entry: {len(top)} band TINGGI dari "
          f"{sum(1 for r in actionable if r.get('rank'))} sinyal terskor"
          + (f" — teratas: {', '.join(r['code'] for r in actionable[:3] if r.get('rank'))}"
             if any(r.get("rank") for r in actionable) else ""),
          file=sys.stderr)

    # Konteks insider + corporate action utk kode actionable (fitur opsional,
    # blok `insider` di settings.yaml). Dilakukan SEBELUM narasi supaya Claude
    # bisa menyebut insider buy/divestasi & RUPS/dividen mendatang di alert.
    _enrich_actionable(client, actionable, date, cfg)

    if narrative_cfg.get("enabled"):
        provider = narrative_cfg.get("provider", "nvidia")
        narrative_key = cfg.narrative_key(provider)
        if not narrative_key and provider not in ("none", "off", "rule"):
            # Dulu ini diam: key kosong -> fallback rule-based tanpa jejak, jadi
            # narasi template terkirim berbulan-bulan tanpa ada yang sadar.
            print(f"[WARN] narrative.provider={provider} tapi API key-nya kosong "
                  f"-> semua narasi pakai rule-based.", file=sys.stderr)
        # Jeda antar panggilan narasi (default 1.2s). NVIDIA NIM membatasi ~40 RPM
        # *best-effort*; 12+ panggilan beruntun tanpa jeda memicu 429 -> tiap sinyal
        # cascade ke model cadangan yang lambat (gemma ~65s) sehingga satu run bisa
        # molor belasan menit. Menyebarkan panggilan menjaganya di bawah limit
        # supaya model utama yang cepat (deepseek ~10-25s) menangani mayoritas.
        pace = float(narrative_cfg.get("pace_seconds", 1.2))
        nstats: dict = {}
        for i, record in enumerate(actionable):
            if i and pace > 0:
                time.sleep(pace)
            record["narrative"] = generate_narrative(
                record["code"], record["state"], record["signals"],
                api_key=narrative_key,
                provider=provider,
                model=narrative_cfg.get("model", ""),
                fallback_models=narrative_cfg.get("fallback_models") or [],
                extra_context=_extra_context(record),
                stats=nstats,
            )
        # Deteksi provider mati. Sebelum ini kegagalan cuma [WARN] per sinyal dan
        # terbukti tak terbaca: semua model NVIDIA di config EOL sejak 7 Agu 2026,
        # 20 run jalan dgn narasi rule-based sampai ketahuan saat audit 9 Sep.
        n_llm, n_fb = nstats.get("llm", 0), nstats.get("fallback", 0)
        if n_fb and n_llm + n_fb:
            share = n_fb / (n_llm + n_fb)
            level = "ERROR" if share >= 0.5 else "WARN"
            print(f"[{level}] NARASI: {n_fb}/{n_llm + n_fb} jatuh ke rule-based "
                  f"({share:.0%}). Model utama: {narrative_cfg.get('model')!r}.",
                  file=sys.stderr)
            if share >= 0.5:
                print("         Provider kemungkinan MATI (EOL/404/timeout). "
                      "Jalankan: python scripts/probe_narrative_models.py",
                      file=sys.stderr)
                ops_alerts.append(
                    f"Narasi LLM mati: {n_fb}/{n_llm + n_fb} sinyal pakai rule-based. "
                    f"Model {narrative_cfg.get('model')} kemungkinan EOL — "
                    f"jalankan probe_narrative_models.py."
                )

    # Log konsol: format gabungan padat (ringkas untuk file log VPS).
    print("\n" + format_alert(scan_date_str, actionable))

    if not args.dry_run and actionable:
        # Kirim PER SINYAL PER CHAT (gaya auto-trading): 1 pesan ramah-awam per
        # saham, didahului 1 baris header. Rate-limit Telegram same-chat ~1 msg/detik
        # -> jeda kecil antar pesan. mark_alert_sent hanya untuk kode yang benar
        # terkirim (bahan evaluasi: bedakan sampai-ke-user vs cuma tercatat).
        try:
            send_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id,
                          format_batch_header(scan_date_str, actionable))
        except Exception as exc:  # noqa: BLE001
            print(f"\n[WARN] gagal kirim header Telegram: {exc}", file=sys.stderr)

        sent_codes: list[str] = []
        for r in actionable:
            try:
                send_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id,
                              format_signal(scan_date_str, r))
                r["alert_sent"] = True
                sent_codes.append(r["code"])
                time.sleep(0.6)   # hormati rate-limit same-chat Telegram
            except Exception as exc:  # noqa: BLE001 — jangan gagalkan sisa batch
                print(f"\n[WARN] gagal kirim {r['code']}: {exc}", file=sys.stderr)

        if sent_codes:
            print(f"\n[OK] {len(sent_codes)} sinyal terkirim ke Telegram "
                  f"({', '.join(sent_codes)}).")
            store.mark_alert_sent(scan_date_str, sent_codes)

    # Peringatan OPERASIONAL — dikirim walau tak ada sinyal, justru karena
    # "tidak ada sinyal" adalah tampilan yang sama persis dengan "engine rusak".
    if ops_alerts:
        body = "\n\n".join(f"• {a}" for a in ops_alerts)
        msg = (f"⚠️ <b>Markup Radar — peringatan sistem</b>\n"
               f"<i>{scan_date_str}</i>\n\n{body}")
        print(f"\n[ERROR] {len(ops_alerts)} peringatan sistem:\n{body}", file=sys.stderr)
        if args.dry_run:
            print("[info] dry-run: peringatan sistem tidak dikirim.", file=sys.stderr)
        else:
            try:
                send_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, msg)
            except Exception as exc:  # noqa: BLE001 — sudah tercetak di log
                print(f"[WARN] gagal kirim peringatan sistem: {exc}", file=sys.stderr)

    # Daftar kode live-watch besok pagi (subset setup MARKUP terbaik, top-N by
    # confidence) -> live_today.txt. Dipakai run_live.sh agar polling fokus & hemat
    # kuota (bukan 50 kode penuh). Ditulis dari hasil scan ini; dilewati saat dry-run.
    _write_live_codes(actionable, cfg, dry_run=args.dry_run)

    # Mirror histori ke Sheets — dilewati saat dry-run agar sheet tak terkotori
    # baris uji. SQLite lokal tetap menyimpan (audit), Sheets = histori lintas run.
    if sink is not None and scan_log:
        if args.dry_run:
            print("[info] dry-run: mirror Google Sheets dilewati.", file=sys.stderr)
        else:
            try:
                n = sink.append_results(scan_log)
                print(f"[OK] {n} baris di-mirror ke Google Sheets.")
            except Exception as exc:  # noqa: BLE001 — sink opsional
                print(f"[WARN] gagal mirror ke Sheets: {exc}", file=sys.stderr)

    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
