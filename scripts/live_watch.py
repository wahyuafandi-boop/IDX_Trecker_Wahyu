#!/usr/bin/env python3
"""Live watch bid/offer (S5) selama market buka — dijalankan MANUAL saat kamu
hadir (pola 2 jam awal + 2 jam jelang tutup, 5 saham).

Beda dengan run_daily (EOD, sekali tembak): ini polling order book tiap N menit
dan baca KOMPOSISI antrian + KONTEKS akumulasi broker (tape-reading bandarmologi):
  - fokus 4 baris teratas ("yang nyata"; level dalam sering diada-ada),
  - lot/order = lot/freq tiap sisi = sidik jari big money vs ritel (order kecil acak),
  - tembok besar di order book AMBIGU -> dibalik oleh status akumulasi (S3 broker,
    ditarik 1x saat start): tembok OFFER + akum = FAKE_OVER (MM tahan harga buat
    nampung = BULLISH); tanpa akum = suplai asli. Tembok BID tanpa akum = FAKE_BID
    (jebakan), dgn akum = demand asli.
  - lacak tembok OFFER antar-siklus (kolom `ovr`): 'v' = mengecil mendadak
    (sering pemicu jebol/entry), '^' = menebal, '=' stabil, 'beku' = order book
    identik persis siklus lalu (data cache/illikuid -> jangan dipercaya penuh).
  - saat tembok menyusut ('v') pada saham akumulasi, cek bar intraday (+1 call)
    untuk memilah CABUT vs DIMAKAN (ajaran tape-reading "liat done detail"):
    'vEAT' = dimakan (harga menyentuh level tembok + ada volume -> demand asli,
    timing entry klasik); 'vCUT' = dicabut (harga tak pernah sentuh level ->
    fake offer terkonfirmasi, tunggu demand nyata). Tak terbedakan -> tetap 'v'.
  - monitor BOW utk saham pantau (state ACCUMULATION_ONGOING di sidecar
    `live_levels.json` dari run_daily): harga (mid bid/offer) masuk zona BOW dua
    siklus berturut + bid top dijaga/di-refill -> ping "BOW-AC" ("BOW after
    confirmasi di bid nya di refil"); mid jebol support -> "Setup Batal".
    One-shot per kode per sesi, 0 call ekstra (pakai order book yang sama).

Verdict: FAKE-OVER+ / DEMAND-REAL (bullish) · FAKE-BID! / supply-real (hindari) ·
nampung++ / DEMAND>> · ritel~~ · seimbang. Threshold di settings.yaml:
queue_bigmoney_lot_per_order, queue_top_levels, queue_wall_pull_drop,
broker_net_buy_streak_min (ambang akumulasi).

KUOTA: 1 call/saham/siklus + 1 call/saham broker sekali di start, + 1 call intraday
HANYA saat kejadian tembok-menyusut di saham akumulasi (jarang). 5 saham, interval
120s, 4 jam ~= 600 call/hari (~12k/bulan, aman di paket Advance 30k). Interval
di-floor 60s; pakai >=120s untuk 5 saham. Cuma jalan saat kamu jalankan.

Contoh:
    python scripts/live_watch.py --probe                 # 1x dump bentuk order_book (verifikasi)
    python scripts/live_watch.py                          # watch watchlist, interval 120s
    python scripts/live_watch.py --codes BBRI BMRI --interval 180
    python scripts/live_watch.py --once                  # 1 siklus lalu keluar
    python scripts/live_watch.py --telegram              # ping saat flip ke bullish / tembok offer dicabut
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.config import load_codes_file, load_settings, parse_codes
from markup_radar.ingest import InvezgoClient
from markup_radar.ingest.broker_client import fetch_broker_daily_net, fetch_closing_queue
from markup_radar.signals.broker_flow import broker_net_buy_streak
from markup_radar.signals.price_volume import (
    queue_composition_verdict,
    queue_imbalance,
    queue_intent_verdict,
    wall_drop_verdict,
)

_MIN_INTERVAL = 60          # floor keras biar tak menjebol kuota
_MAX_FAIL_CYCLES = 5        # auto-stop kalau sekian siklus berturut SEMUA saham gagal
                            # (tanda jaringan/Invezgo down) — penting saat unattended
_TAG = {
    # kontekstual (intent) — butuh status akumulasi broker:
    "FAKE_OVER":       "FAKE-OVER+",  # tembok offer + akum -> MM nampung = BULLISH
    "DEMAND_REAL":     "DEMAND-REAL",  # tembok bid + akum -> demand asli = bullish
    "SUPPLY_REAL":     "supply-real",  # tembok offer tanpa akum -> suplai asli (hindari)
    "FAKE_BID":        "FAKE-BID!!",  # tembok bid tanpa akum -> ilusi demand = JEBAKAN
    # komposisi (fallback saat tak ada tembok big money):
    "DEMAND_DOMINAN":  "DEMAND >>",
    "PASSIVE_ACCUM":   "nampung++",
    "PASSIVE_DISTRIB": "jual-diam",
    "SEIMBANG":        "seimbang",
    "SUPPLY_DOMINAN":  "SUPPLY <<",
    "RITEL_NOISE":     "ritel~~",
    "NO_DATA":         "no-data",
}
# Verdict bullish (untuk highlight & ping Telegram).
_BULLISH = {"FAKE_OVER", "DEMAND_REAL", "DEMAND_DOMINAN", "PASSIVE_ACCUM"}


def _trend(curr: float, prev: float | None) -> str:
    if prev is None:
        return " "
    if curr > prev * 1.02:
        return "+"     # demand menguat
    if curr < prev * 0.98:
        return "-"     # demand melemah
    return "="


def _wall_trend(curr: float, prev: float | None, drop: float) -> str:
    """Lacak tembok (lot top) antar-siklus. 'v' = mengecil mendadak (dicabut/dimakan,
    sering pemicu jebol); '^' = menebal; '=' stabil."""
    if prev is None or prev <= 0:
        return " "
    if curr <= prev * (1 - drop):
        return "v"
    if curr >= prev * (1 + drop):
        return "^"
    return "="


def _probe(client: InvezgoClient, codes: list[str]) -> int:
    """Tarik order_book 1x per kode lalu cetak bentuk mentah + hasil parse.
    Dipakai sekali (di hari bursa, setelah ada antrian) untuk verifikasi shape."""
    for code in codes:
        print(f"\n===== {code} : raw order_book =====")
        try:
            raw = client.order_book(code)
            print(json.dumps(raw, indent=2, ensure_ascii=False)[:2000])
            q = fetch_closing_queue(client, code)
            imb = queue_imbalance(q["bid_top_lot"], q["offer_top_lot"])
            read = queue_composition_verdict(
                q["bid_top_lot"], q["bid_top_freq"],
                q["offer_top_lot"], q["offer_top_freq"])
            print(f"-> top4 BID lot={q['bid_top_lot']:.0f} freq={q['bid_top_freq']:.0f} "
                  f"(lot/order {q['bid_lot_per_order']:.1f})")
            print(f"-> top4 OFF lot={q['offer_top_lot']:.0f} freq={q['offer_top_freq']:.0f} "
                  f"(lot/order {q['offer_lot_per_order']:.1f})")
            print(f"-> imbalance(top4)={imb:.2f}  read={read}")
            print(f"-> all-level sum: bid={q['bid_volume']:.0f} offer={q['offer_volume']:.0f} "
                  f"(levels bid={q['n_bid_levels']:.0f}/offer={q['n_offer_levels']:.0f})")
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] {code}: {exc}")
        print(f"===== {code} : raw intraday_chart (verifikasi cabut-vs-dimakan) =====")
        try:
            print(json.dumps(client.intraday_chart(code), indent=2,
                             ensure_ascii=False)[:1200])
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] intraday {code}: {exc}")
    print("\nCek: (1) apakah ADA field *freq per level? lot/order = 0 berarti freq "
          "tak ada -> verdict komposisi tumpul. (2) urutan level: indeks 0 = harga "
          "terbaik? Kalau shape beda, sesuaikan _order_levels/fetch_closing_queue. "
          "(3) intraday: ada field time/high/volume per bar? dipakai wall_drop_verdict.")
    return 0


def _load_levels(cfg) -> dict:
    """Level BOW/entry per kode dari sidecar run_daily (`live_levels.json`).

    Fail-soft {}: file absen/korup -> monitor BOW nonaktif, polling biasa tetap
    jalan (kompatibel dgn live_today.txt lama tanpa sidecar)."""
    path = Path(cfg.live_watch.get("levels_file", "live_levels.json"))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _mid_price(q: dict) -> float:
    """Proxy harga terkini dari order book: mid bid/offer terbaik (0 bila kosong)."""
    bid, off = q.get("bid_best_price", 0.0), q.get("offer_best_price", 0.0)
    if bid > 0 and off > 0:
        return (bid + off) / 2
    return bid or off or 0.0


def _bow_check(lv: dict, mid: float, bid_lot: float, prev: dict,
               hold_ratio: float) -> tuple[str | None, bool]:
    """Cek zona BOW satu siklus utk saham pantau. Return (event, in_zone).

    event "AC"      : harga di zona BOW dua siklus berturut DAN bid top dijaga/
                      di-refill (>= hold_ratio x siklus lalu) — operasionalisasi
                      "BOW after confirmasi di bid nya di refil".
    event "INVALID" : mid jatuh di bawah support -> setup akumulasi batal.
    event None      : belum ada kejadian (baru masuk zona, bid terkuras, dst).
    """
    if not lv or mid <= 0:
        return None, False
    support = float(lv.get("support") or 0.0)
    bow_lo = float(lv.get("bow_lo") or support)
    bow_hi = float(lv.get("bow_hi") or 0.0)
    if support <= 0 or bow_hi <= 0:
        return None, False
    if mid < support:
        return "INVALID", False
    in_zone = bow_lo <= mid <= bow_hi
    prev_bid = float(prev.get("bid_lot") or 0.0)
    if (in_zone and prev.get("bow_in_zone") and prev_bid > 0
            and bid_lot >= prev_bid * hold_ratio):
        return "AC", in_zone
    return None, in_zone


def _classify_wall_drop(client, code: str, prev_entry: dict) -> str | None:
    """Tembok offer menyusut — DIMAKAN atau DICABUT? Cek bar intraday sejak siklus
    lalu (+1 call, hanya saat kejadian). Fail-soft: None bila data/shape tak
    mendukung (harga tembok tak kebaca, endpoint error, timestamp tak terparse)."""
    wall_price = float(prev_entry.get("offer_price") or 0.0)
    since = prev_entry.get("ts")
    if wall_price <= 0:
        return None
    try:
        raw = client.intraday_chart(code)
    except Exception:  # noqa: BLE001 — klasifikasi opsional, jangan ganggu siklus
        return None
    bars = raw if isinstance(raw, list) else (
        (raw or {}).get("items") or (raw or {}).get("data") or [])
    return wall_drop_verdict(bars, wall_price, since=since)


def _accumulation_flags(client, codes, cfg) -> tuple[dict[str, bool], dict[str, str]]:
    """Status akumulasi per kode dari S3 broker net-buy streak — ditarik SEKALI di
    start (akumulasi bergerak lambat/harian, tak perlu tiap siklus). 1 call/saham.

    Return (flag, label): flag[code]=True bila streak >= broker_net_buy_streak_min;
    label dipakai untuk display ("AKUM"/"no-akum"/"?" bila data tak ada)."""
    streak_min = int(cfg.thresholds.get("broker_net_buy_streak_min", 3))
    today = dt.date.today()
    dfrom = (today - dt.timedelta(days=21)).isoformat()   # ~ cukup utk streak harian
    dto = today.isoformat()
    flags: dict[str, bool] = {}
    labels: dict[str, str] = {}
    for code in codes:
        try:
            nets = fetch_broker_daily_net(client, code, dfrom, dto, top_n=cfg.broker_top_n)
        except Exception:  # noqa: BLE001 — degrade ke "unknown" (konservatif)
            nets = []
        if not nets:
            flags[code], labels[code] = False, "?"
            continue
        streak = broker_net_buy_streak(nets)
        flags[code] = streak >= streak_min
        labels[code] = f"AKUM/{streak}d" if flags[code] else "no-akum"
    return flags, labels


def _cycle(client, codes, cfg, prev, last_verdict, accum, accum_lbl,
           *, levels, bow_done, send_tg, token, chat_id) -> int:
    """Satu siklus polling semua kode. Return jumlah kode yang BERHASIL ditarik
    (0 = semua gagal -> dipakai main() untuk deteksi network down & auto-stop).

    `levels` = sidecar live_levels.json (monitor BOW utk state pantau);
    `bow_done` = kode yang event BOW-nya sudah dilaporkan (one-shot per sesi)."""
    from markup_radar.alert import format_live_bow, format_live_signal, send_telegram

    demand = float(cfg.thresholds.get("queue_imbalance_demand", 1.0))
    bigmoney = float(cfg.thresholds.get("queue_bigmoney_lot_per_order", 20.0))
    top_lv = int(cfg.thresholds.get("queue_top_levels", 4))
    wall_drop = float(cfg.thresholds.get("queue_wall_pull_drop", 0.35))
    ts = dt.datetime.now().strftime("%H:%M:%S")
    print(f"\n[{ts}]  demand>={demand:g}  big-money lot/order>={bigmoney:g}  "
          f"wall-drop>={wall_drop:g}  (top-{top_lv}, akum=S3 broker)")
    print(f"  {'code':6s} {'akum':8s} {'bidT':>9s} {'offT':>9s} {'imb':>6s} "
          f"{'b/ord':>7s} {'o/ord':>7s}  {'read':11s} {'ovr':4s} trend")
    ok = 0
    for code in codes:
        try:
            q = fetch_closing_queue(client, code, top_levels=top_lv)
            imb = queue_imbalance(q["bid_top_lot"], q["offer_top_lot"])
            verdict = queue_intent_verdict(
                q["bid_top_lot"], q["bid_top_freq"],
                q["offer_top_lot"], q["offer_top_freq"],
                accumulating=accum.get(code, False),
                demand=demand, bigmoney_lot_per_order=bigmoney)
        except Exception as exc:  # noqa: BLE001 — jangan hentikan loop
            print(f"  {code:6s} [WARN] {exc}")
            continue
        ok += 1
        p = prev.get(code, {})
        arrow = _trend(imb, p.get("imb"))
        # Sidik jari order book — kalau identik persis siklus lalu = data beku/cache
        # (saham illikuid sering begini), bacaan jangan dipercaya penuh.
        fp = (q["bid_top_lot"], q["bid_top_freq"], q["offer_top_lot"],
              q["offer_top_freq"], q["bid_volume"], q["offer_volume"])
        stale = p.get("fp") is not None and fp == p["fp"]
        owall = "beku" if stale else _wall_trend(q["offer_top_lot"], p.get("owall"), wall_drop)
        # Tembok menyusut di saham akumulasi -> pilah CABUT vs DIMAKAN via bar
        # intraday (+1 call hanya saat kejadian; None bila tak terbedakan).
        wall_pulled = owall == "v" and accum.get(code, False)
        wall_verdict = _classify_wall_drop(client, code, p) if wall_pulled else None

        # Monitor BOW (state pantau, level dari sidecar EOD) — one-shot per sesi.
        lv_bow = levels.get(code) or {}
        bow_event, bow_in_zone = (None, False)
        if lv_bow.get("state") == "ACCUMULATION_ONGOING" and code not in bow_done:
            hold = float(cfg.live_watch.get("bow_bid_hold_ratio", 0.9))
            bow_event, bow_in_zone = _bow_check(
                lv_bow, _mid_price(q), q["bid_top_lot"], p, hold)

        prev[code] = {"imb": imb, "owall": q["offer_top_lot"], "fp": fp,
                      "offer_price": q.get("offer_best_price", 0.0),
                      "bid_lot": q["bid_top_lot"], "bow_in_zone": bow_in_zone,
                      "ts": dt.datetime.now()}
        owall_disp = {"EATEN": "vEAT", "PULLED": "vCUT"}.get(wall_verdict, owall)
        print(f"  {code:6s} {accum_lbl.get(code, '?'):8s} "
              f"{q['bid_top_lot']:>9.0f} {q['offer_top_lot']:>9.0f} {imb:>6.2f} "
              f"{q['bid_lot_per_order']:>7.1f} {q['offer_lot_per_order']:>7.1f}  "
              f"{_TAG[verdict]:11s} {owall_disp:4s} {arrow}")
        if bow_event:
            bow_done.add(code)
            tag = ("harga masuk zona BOW + bid dijaga" if bow_event == "AC"
                   else "jebol support — setup batal")
            print(f"         ↳ BOW-{bow_event}: {tag} "
                  f"(zona {lv_bow.get('bow_lo')}-{lv_bow.get('bow_hi')}, "
                  f"support {lv_bow.get('support')})")
            if send_tg:
                try:
                    send_telegram(token, chat_id, format_live_bow(
                        code, kind=bow_event,
                        bow_lo=float(lv_bow.get("bow_lo") or 0),
                        bow_hi=float(lv_bow.get("bow_hi") or 0),
                        support=float(lv_bow.get("support") or 0),
                        time_str=ts[:5],
                    ))
                except Exception as exc:  # noqa: BLE001
                    print(f"  [WARN] telegram: {exc}")

        # Telegram opsional, hindari spam: (a) TRANSISI ke verdict bullish, atau
        # (b) tembok offer dicabut/mengecil ('v') saat saham lagi akumulasi
        # (pemicu jebol klasik fake-over).
        flip_bullish = verdict in _BULLISH and last_verdict.get(code) not in _BULLISH
        if send_tg and (flip_bullish or wall_pulled):
            # Pesan ramah-awam (gaya auto-trading); flip bullish diprioritaskan
            # atas wall-pulled bila keduanya kebetulan sama-sama benar.
            # Angka konkret tembok diambil dari siklus SEBELUMNYA (`p`): itulah
            # kondisi tembok sebelum menyusut. `offer_price` = harga tempat
            # tembok berdiri; `owall` = ukurannya. Level entry/SL dari sidecar
            # EOD supaya pesan bisa langsung bilang "sudah lewat entry atau belum".
            lv = levels.get(code) or {}
            try:
                send_telegram(token, chat_id, format_live_signal(
                    code,
                    verdict=verdict if flip_bullish else None,
                    wall_pulled=not flip_bullish,
                    wall_verdict=wall_verdict if not flip_bullish else None,
                    imb=imb,
                    accum_label=accum_lbl.get(code, ""),
                    time_str=ts[:5],
                    wall_price=p.get("offer_price") or q.get("offer_best_price"),
                    wall_before=p.get("owall"),
                    wall_after=q["offer_top_lot"],
                    price_now=_mid_price(q),
                    entry=lv.get("entry"),
                    stop_loss=lv.get("support"),
                ))
            except Exception as exc:  # noqa: BLE001
                print(f"  [WARN] telegram: {exc}")
        last_verdict[code] = verdict
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Live watch bid/offer (S5) saat market buka")
    ap.add_argument("--codes", nargs="+", metavar="CODE", help="override watchlist")
    ap.add_argument("--codes-file", metavar="PATH",
                    help="baca watchlist dari file teks (1 kode/baris, hasil screening "
                         "Stockbit). Dikalahkan oleh --codes.")
    ap.add_argument("--interval", type=int, default=120, help="detik antar siklus (floor 60)")
    ap.add_argument("--probe", action="store_true", help="dump bentuk order_book 1x lalu keluar")
    ap.add_argument("--once", action="store_true", help="1 siklus lalu keluar")
    ap.add_argument("--duration", type=int, default=0, help="auto-stop setelah N menit (0=manual)")
    ap.add_argument("--telegram", action="store_true",
                    help="ping saat flip ke bullish / tembok offer dicabut")
    args = ap.parse_args()

    cfg = load_settings()
    if args.codes:
        codes = parse_codes(args.codes)
    elif args.codes_file:
        try:
            codes = load_codes_file(args.codes_file)
        except OSError as exc:
            print(f"[ERROR] gagal baca --codes-file: {exc}", file=sys.stderr)
            return 1
        print(f"[info] watchlist dari {args.codes_file}: {', '.join(codes)}", file=sys.stderr)
    else:
        codes = cfg.watchlist
    if not codes:
        print("[ERROR] tidak ada kode untuk dipantau.", file=sys.stderr)
        return 1
    if not cfg.invezgo_api_key:
        print("[ERROR] INVEZGO_API_KEY belum di-set di .env", file=sys.stderr)
        return 1

    client = InvezgoClient(cfg.invezgo_api_key, cfg.invezgo_base_url,
                           rate_limit_per_min=cfg.rate_limit_per_min)

    if args.probe:
        return _probe(client, codes)

    interval = max(args.interval, _MIN_INTERVAL)
    if interval != args.interval:
        print(f"[info] interval dinaikkan ke {interval}s (floor kuota).", file=sys.stderr)
    calls_hr = int(len(codes) * 3600 / interval)
    print(f"Live watch: {', '.join(codes)} | interval {interval}s | ~{calls_hr} call/jam")
    try:
        print(f"[info] kuota API: {json.dumps(client.api_usage())}", file=sys.stderr)
    except Exception:  # noqa: BLE001 — usage opsional
        pass
    print("Ctrl+C untuk berhenti.")

    # Status akumulasi (S3 broker) ditarik sekali — konteks utk bedakan fake-over
    # (bullish) vs suplai asli, dan fake-bid (jebakan) vs demand asli.
    print(f"[info] ambil status akumulasi broker (1 call/saham) ...", file=sys.stderr)
    accum, accum_lbl = _accumulation_flags(client, codes, cfg)
    print(f"[info] akumulasi: "
          f"{', '.join(f'{c}={accum_lbl[c]}' for c in codes)}", file=sys.stderr)

    # Level BOW/entry dari sidecar EOD (opsional; {} = monitor BOW nonaktif).
    levels = _load_levels(cfg)
    watch_bow = [c for c in codes
                 if (levels.get(c) or {}).get("state") == "ACCUMULATION_ONGOING"]
    if watch_bow:
        print(f"[info] monitor BOW aktif: {', '.join(watch_bow)}", file=sys.stderr)

    prev: dict[str, dict] = {}
    last_verdict: dict[str, str] = {}
    bow_done: set[str] = set()
    deadline = time.monotonic() + args.duration * 60 if args.duration else None
    fail_streak = 0
    try:
        while True:
            ok = _cycle(client, codes, cfg, prev, last_verdict, accum, accum_lbl,
                        levels=levels, bow_done=bow_done, send_tg=args.telegram,
                        token=cfg.telegram_bot_token, chat_id=cfg.telegram_chat_id)
            # Auto-stop saat jaringan/Invezgo down (penting saat unattended/terjadwal):
            # kalau SEMUA saham gagal beberapa siklus berturut, berhenti — jangan
            # muter percuma berjam-jam (kejadian 16:08 kemarin: WARN 25 menit).
            fail_streak = fail_streak + 1 if ok == 0 else 0
            if fail_streak >= _MAX_FAIL_CYCLES:
                print(f"\n[info] {fail_streak}x siklus berturut semua saham gagal "
                      f"(jaringan/Invezgo down?) — berhenti otomatis.", file=sys.stderr)
                break
            if args.once:
                break
            if deadline and time.monotonic() >= deadline:
                print("\n[info] durasi tercapai, berhenti.")
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[info] dihentikan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
