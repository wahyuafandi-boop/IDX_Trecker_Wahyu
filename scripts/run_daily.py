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
import os
import sys
import tempfile
from pathlib import Path

# Izinkan import 'markup_radar' tanpa install (src layout).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.alert import format_alert, send_telegram
from markup_radar.config import load_codes_file, load_settings, parse_codes
from markup_radar.ingest import InvezgoClient
from markup_radar.ingest.broker_client import (
    fetch_broker_daily_net,
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
from markup_radar.scoring import classify, confidence_markup_start
from markup_radar.signals import StockData, compute_signals
from markup_radar.signals.levels import compute_trade_levels
from markup_radar.signals.market import market_regime
from markup_radar.store import Store, build_sink


def _date_range(end: dt.date, days_back: int) -> tuple[str, str]:
    start = end - dt.timedelta(days=days_back)
    return start.isoformat(), end.isoformat()


def _write_live_codes(actionable: list[dict], cfg, *, dry_run: bool) -> list[str]:
    """Pilih subset kode untuk live-watch besok pagi dari hasil scan EOD.

    Ambil setup MARKUP (CONFIRMED + START) — kandidat entry yang perlu konfirmasi
    order book live — urut confidence tertinggi (CONFIRMED cenderung naik sendiri
    karena conf-nya tinggi), lalu cap `max_codes`. Tulis atomik ke `live_today.txt`
    supaya run_live.sh polling FOKUS ke setup terbaik, bukan seluruh universe 50 kode
    (hemat ~80% kuota live tanpa kehilangan nilai trading).

    File SELALU ditulis (mencerminkan setup malam ini): nol setup -> file hanya header
    (tanpa kode) -> run_live besok tak punya kode -> exit cepat (0 call). Dilewati
    saat dry-run agar file produksi tak terkotori data uji.
    """
    lw = cfg.live_watch
    states = set(lw.get("include_states", ["MARKUP_CONFIRMED", "MARKUP_START"]))
    max_codes = int(lw.get("max_codes", 5))
    out_path = Path(lw.get("out_file", "live_today.txt"))

    picks = [r for r in actionable if r.get("state") in states]
    picks.sort(key=lambda r: r.get("confidence", 0), reverse=True)
    codes = [r["code"] for r in picks[:max_codes]]

    print(f"[info] live-watch besok ({len(codes)}/{max_codes} kode): "
          f"{', '.join(codes) or '(kosong)'}", file=sys.stderr)
    if dry_run:
        print("[info] dry-run: live_today.txt tidak ditulis.", file=sys.stderr)
        return codes

    header = (
        f"# auto-generated oleh run_daily.py @ "
        f"{dt.datetime.now().isoformat(timespec='seconds')}\n"
        f"# {len(codes)} kode live-watch (top {max_codes} by confidence "
        f"dari {len(actionable)} actionable)\n"
    )
    body = ("\n".join(codes) + "\n") if codes else ""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out_path.parent), prefix=".live_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(header + body)
        os.replace(tmp, out_path)  # atomic: tak ada file separuh
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print(f"[OK] {len(codes)} kode live ditulis ke {out_path}", file=sys.stderr)
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
    prio = {"MARKUP_CONFIRMED": 0, "MARKUP_START": 1}
    ranked = sorted(
        actionable,
        key=lambda r: (prio.get(r["state"], 9), -r.get("confidence", 0)),
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

    ohlcv = fetch_ohlcv(client, code, ohlc_from, ohlc_to)              # 1
    done = fetch_done_breakdown(client, code, date.isoformat())       # 2
    queue = fetch_closing_queue(client, code)                         # 3
    # Streak S3 dari 1 call inventory-chart (bukan loop per hari).
    daily_net = fetch_broker_daily_net(client, code, *_date_range(date, streak_lb))  # 4

    return StockData(
        code=code,
        ohlcv=ohlcv,
        done_offer_value=done["done_offer_value"],
        done_bid_value=done["done_bid_value"],
        broker_daily_net=daily_net,
        closing_bid_volume=queue["bid_volume"],
        closing_offer_volume=queue["offer_volume"],
        ihsg_close=ihsg_close,
    )


def evaluate(data: StockData, signals: dict, cfg, eff: dict):
    """Klasifikasi + confidence + trade levels untuk satu saham (pure, tanpa network).

    `eff` = thresholds dasar + overlay profil regime (di-resolve sekali per run di
    main()). Trade levels HANYA dihitung untuk state MARKUP_* (spec D5); state lain
    -> None. atr_mult_sl & rr_target diambil dari profil (`eff`), sisanya dari
    blok `levels` config.
    """
    state = classify(signals, eff)
    conf = confidence_markup_start(signals, cfg.score_weights)
    levels = None
    if state in ("MARKUP_START", "MARKUP_CONFIRMED"):
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

    # Data market-wide: IHSG ditarik 1x per run, dipakai semua saham (hemat kuota).
    ihsg = fetch_ihsg(client, *_date_range(date, cfg.windows.get("ihsg_ma", 50) * 2))
    ihsg_close = ihsg["close"] if not ihsg.empty else None

    # Regime selector (spec §4.6): IHSG vs MA -> profil parameter, di-resolve SEKALI
    # per run. eff = thresholds dasar + overlay profil regime (rvol/RS-gate/SL/RR).
    # Fail-safe: IHSG kosong -> market_regime balik BEARISH (profil lebih ketat).
    regime = market_regime(ihsg_close, cfg.windows.get("ihsg_ma", 50))
    eff = {**cfg.thresholds, **cfg.regime_profiles.get(regime.value, {})}
    print(f"[info] regime: {regime.value} "
          f"(rvol_spike={eff.get('rvol_spike')}, "
          f"require_rs={eff.get('require_relative_strength', False)})", file=sys.stderr)

    scan_log: list[dict] = []   # SEMUA kode (termasuk NEUTRAL) untuk mirror Sheets
    actionable: list[dict] = []
    for code in cfg.watchlist:
        try:
            data = build_stock_data(
                client, code, date, cfg, ihsg_close=ihsg_close,
            )
            signals = compute_signals(data, cfg.thresholds, cfg.windows, cfg.broker_top_n)
            state, conf, levels = evaluate(data, signals, cfg, eff)
        except Exception as exc:  # noqa: BLE001 — jangan gagalkan seluruh batch
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
        }
        if state in cfg.alert_states:
            actionable.append(record)
        scan_log.append(record)

    # Konteks insider + corporate action utk kode actionable (fitur opsional,
    # blok `insider` di settings.yaml). Dilakukan SEBELUM narasi supaya Claude
    # bisa menyebut insider buy/divestasi & RUPS/dividen mendatang di alert.
    _enrich_actionable(client, actionable, date, cfg)

    if narrative_cfg.get("enabled"):
        for record in actionable:
            record["narrative"] = generate_narrative(
                record["code"], record["state"], record["signals"],
                api_key=cfg.anthropic_api_key,
                model=narrative_cfg.get("model", "claude-opus-4-8"),
                extra_context=_extra_context(record),
            )

    msg = format_alert(scan_date_str, actionable)
    print("\n" + msg)

    if not args.dry_run and actionable:
        try:
            send_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, msg)
            print("\n[OK] alert terkirim ke Telegram.")
            # Catat status kirim di record (mirror Sheets) + SQLite — bahan
            # evaluasi: bedakan sinyal yang benar-benar sampai ke user vs
            # yang cuma tercatat (dry-run / Telegram gagal).
            for r in actionable:
                r["alert_sent"] = True
            store.mark_alert_sent(scan_date_str, [r["code"] for r in actionable])
        except Exception as exc:  # noqa: BLE001
            print(f"\n[WARN] gagal kirim Telegram: {exc}", file=sys.stderr)

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
