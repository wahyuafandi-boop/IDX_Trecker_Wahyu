"""Format & kirim watchlist harian ke Telegram (spec §6, Phase 4).

Pakai parse_mode HTML (bukan Markdown) supaya nama state yang mengandung
underscore (mis. MARKUP_START) tidak memecah parser Telegram.
"""

from __future__ import annotations

import html

import requests

from markup_radar.ingest.ownership_client import fmt_rp
from markup_radar.signals.levels import bow_zone

_EMOJI = {
    "MARKUP_CONFIRMED": "✅",
    "MARKUP_START": "🚀",
    "ACCUMULATION_ONGOING": "🟡",
    "DISTRIBUTION_WARNING": "🔻",
}

# --- Pesan ramah-awam per sinyal (gaya auto-trading, spec UX 2026-07-07) ---
_MONTHS_ID = ["", "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
              "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]

# state -> (emoji, label ramah, penjelasan awam satu kalimat).
_STATE_INFO = {
    "MARKUP_CONFIRMED": (
        "✅", "Markup Terkonfirmasi",
        "Bandar sudah selesai kumpulin barang dan antrian beli menumpuk — "
        "ini setup paling siap naik.",
    ),
    "MARKUP_START": (
        "🚀", "Awal Markup",
        "Bandar mulai angkat harga — pembeli agresif dan broker masih borong.",
    ),
    "ACCUMULATION_ONGOING": (
        "🟡", "Akumulasi Berlangsung",
        "Bandar diam-diam mengumpulkan barang selagi harga ranging — "
        "belum saatnya naik, cukup dipantau dulu.",
    ),
    "DISTRIBUTION_WARNING": (
        "🔻", "Waspada Distribusi",
        "Penjual menguasai dan broker berbalik jual di area puncak — "
        "risiko turun, hati-hati.",
    ),
}
_MARKUP_STATES = ("MARKUP_CONFIRMED", "MARKUP_START")


def _fmt_date_id(iso: str) -> str:
    """'2026-07-06' -> '6 Jul 2026'. Fallback ke input mentah bila gagal parse."""
    try:
        y, m, d = (int(x) for x in iso.split("-"))
        return f"{d} {_MONTHS_ID[m]} {y}"
    except (ValueError, IndexError):
        return iso


def _why_bullets(s: dict, own: dict | None) -> list[str]:
    """Terjemahkan sinyal teknis (done_ratio/rvol/close/streak/ownership) jadi
    poin bahasa awam untuk blok 'Kenapa masuk radar'."""
    out: list[str] = []
    dr = s.get("done_ratio")
    if dr is not None:
        pct = dr * 100
        if dr >= 0.55:
            out.append(f"Pembeli agresif menyerap barang ({pct:.0f}% transaksi di sisi beli)")
        elif dr <= 0.45:
            out.append(f"⚠️ Penjual masih lebih aktif ({pct:.0f}% transaksi di sisi beli)")
        else:
            out.append(f"Tekanan beli-jual relatif seimbang ({pct:.0f}% di sisi beli)")
    rvol = s.get("rvol")
    if rvol:
        tag = " (ramai)" if rvol >= 2 else (" (sepi)" if rvol < 1 else "")
        out.append(f"Volume {rvol:.1f}× rata-rata{tag}")
    cir = s.get("close_in_range")
    if cir is not None:
        if cir >= 0.6:
            out.append("Harga tutup kuat, dekat puncak hari")
        elif cir <= 0.4:
            out.append("⚠️ Harga tutup lemah, di bawah rentang hari")
        else:
            out.append("Harga tutup di tengah rentang hari")
    streak = s.get("broker_net_buy_streak", 0)
    if streak >= 1:
        out.append(f"Broker borong {streak} hari beruntun")
    # S11 compatibility: yang borong memang penggerak harga, atau cuma numpuk?
    corr = s.get("flow_price_corr")
    if corr is not None:
        if corr >= 0.5:
            out.append(f"Harga terbukti bergerak searah broker yang borong "
                       f"(compatibility {corr:.0%})")
        elif corr <= 0.1 and streak >= 2:
            out.append(f"⚠️ Broker borong tapi harga belum mengikuti "
                       f"(compatibility rendah, {corr:.0%})")
    if own:
        # Teori float control: makin KECIL porsi ritel makin bagus (barang
        # terkunci di bandar) — jangan pakai kata "cuma" saat angkanya besar.
        pct_r = own["retail_pct"]
        if pct_r < 20:
            seg = (f"Ritel cuma pegang {pct_r:.1f}% saham — "
                   "barang relatif terkunci di tangan kuat (poin plus)")
        elif pct_r >= 50:
            seg = (f"⚠️ Mayoritas saham ({pct_r:.1f}%) di tangan ritel — "
                   "float belum terkontrol bandar (poin minus)")
        else:
            seg = f"Ritel pegang {pct_r:.1f}% saham"
        pp = own.get("retail_trend_pp")
        if pp is not None and own.get("trend_months"):
            if pp < 0:
                seg += f"; porsi ritel menyusut {abs(pp):.1f} poin (barang pindah ke tangan kuat)"
            elif pp > 0:
                seg += f"; porsi ritel membengkak {pp:.1f} poin (⚠️ indikasi distribusi)"
        out.append(seg)
    return out


def format_alert(date: str, items: list[dict]) -> str:
    """items: list of {code, state, confidence, signals}. -> string HTML."""
    if not items:
        return f"<b>Markup Radar</b> — {html.escape(date)}\nTidak ada sinyal actionable hari ini."

    lines = [f"<b>Markup Radar</b> — {html.escape(date)}", ""]
    # Urutkan: MARKUP_CONFIRMED dulu (tier konfirmasi), lalu MARKUP_START,
    # selanjutnya confidence tertinggi.
    order = {
        "MARKUP_CONFIRMED": 0,
        "MARKUP_START": 1,
        "ACCUMULATION_ONGOING": 2,
        "DISTRIBUTION_WARNING": 3,
    }
    items = sorted(items, key=lambda x: (order.get(x["state"], 9), -x.get("confidence", 0)))

    for it in items:
        s = it.get("signals", {})
        emoji = _EMOJI.get(it["state"], "•")

        # Header: kode — state (conf N) [· REGIME] [· RS ±x.x%].
        head = (
            f"{emoji} <b>{html.escape(str(it['code']))}</b> — "
            f"{html.escape(str(it['state']))} (conf {it.get('confidence', 0)})"
        )
        if it.get("regime"):
            head += f" · {html.escape(str(it['regime']))}"
        if "relative_strength" in it:
            head += f" · RS {it['relative_strength']:+.1%}"
        lines.append(head)

        # Baris sinyal dasar (+ S11 compatibility bila ada bacaan).
        base = (
            f"   done {s.get('done_ratio', 0):.2f} · "
            f"RVOL {s.get('rvol', 0):.1f}x · "
            f"close {s.get('close_in_range', 0):.2f} · "
            f"streak {s.get('broker_net_buy_streak', 0)}"
        )
        if s.get("flow_price_corr") is not None:
            base += f" · comp {s['flow_price_corr']:.2f}"
        lines.append(base)

        # Baris level — terisi utk MARKUP_* (rencana trade, spec D5/§4.8) dan
        # ACCUMULATION_ONGOING (panduan pantau; entry = bersyarat breakout).
        # DISTRIBUTION -> levels None -> tampil tanpa entry.
        lv = it.get("levels")
        if lv:
            lines.append(
                f"   📍 Resis {lv['resistance']:g} · Support {lv['support']:g} · "
                f"ATR {lv['atr']:g}"
            )
            lines.append(
                f"   🎯 Entry &gt;{lv['entry']:g} · "
                f"SL {lv['stop_loss']:g} (-{lv['stop_pct']:.1%}) · "
                f"TP {lv['take_profit']:g} (R:R {lv['rr_realized']:.1f}) · "
                f"~hold {lv['est_hold_days']}d"
            )

        # Insider 30d (kalau ada laporan di window): + = akumulasi orang dalam
        # (konfirmasi), - = divestasi (red flag saat sinyal MARKUP).
        ins = it.get("insider")
        if ins:
            net = ins.get("net_change_pct", 0.0)
            dot = "🟢" if net > 0 else ("🔴" if net < 0 else "⚪")
            detail = f"{ins.get('n_reports', 0)} laporan"
            if ins.get("last_purpose"):
                detail += f", terakhir {html.escape(str(ins['last_purpose']))}"
            if ins.get("last_date"):
                detail += f" {html.escape(str(ins['last_date']))}"
            lines.append(
                f"   👤 Insider {ins.get('window_days', 30)}d: "
                f"{dot} {net:+.2f} pp ({detail})"
            )

        # Corporate action mendatang (window ~horizon hold): RUPS/dividen/dst.
        ca = it.get("corp_actions")
        if ca:
            lines.append(
                "   📅 "
                + " · ".join(
                    f"{html.escape(str(c['label']))} {html.escape(str(c['date']))}"
                    for c in ca[:3]
                )
            )

        # Float control (KSEI bulanan): porsi ritel kecil & menyusun = supply
        # terkunci, gampang di-markup; ritel membengkak = distribusi.
        own = it.get("ownership")
        if own:
            seg = f"   🏦 Ritel {own['retail_pct']:.1f}%"
            if own.get("retail_float_pct") is not None:
                seg += f" ({own['retail_float_pct']:.0f}% FF)"
            if own.get("controlling_pct"):
                seg += f" · pengendali {own['controlling_pct']:.1f}%"
            if own.get("trend_months"):
                pp = own.get("retail_trend_pp", 0.0)
                arrow = "▼" if pp < 0 else ("▲" if pp > 0 else "→")
                seg += f" · {arrow}{abs(pp):.1f}pp/{own['trend_months']}bln"
            lines.append(seg)

        # Rotasi net-flow per kategori broker hari scan (heuristik mapping
        # settings.yaml): ritel minus + asing/smart plus = rotasi bullish.
        rot = it.get("rotation")
        if rot:
            lines.append(
                f"   🔄 Ritel {fmt_rp(rot['retail_net'])} · "
                f"Asing {fmt_rp(rot['foreign_net'])} · "
                f"Smart {fmt_rp(rot['smart_net'])}"
            )

        if it.get("narrative"):
            lines.append(f"   <i>{html.escape(str(it['narrative']))}</i>")
    lines.append("")
    lines.append("<i>Setup swing 10–20 hari (regime-aware). Entry = breakout "
                 "terkonfirmasi, bukan harga sekarang. Kelola risiko sendiri.</i>")
    return "\n".join(lines)


def format_batch_header(date: str, items: list[dict]) -> str:
    """Satu baris pembuka sebelum rangkaian pesan per-sinyal (dikirim run_daily)."""
    n = len(items)
    return (
        f"📡 <b>Markup Radar</b> · {_fmt_date_id(date)}\n"
        f"{n} sinyal terpantau hari ini — rincian menyusul per saham 👇"
    )


def format_signal(date: str, it: dict) -> str:
    """Format SATU sinyal jadi pesan Telegram ramah-awam (HTML), gaya auto-trading.

    Dikirim per-chat (satu pesan per saham) oleh run_daily — pengganti blok
    teknis padat lama. `format_alert` (gabungan) tetap dipakai untuk log konsol.
    Semua string dari data di-escape; angka harga/level aman (numerik).
    """
    code = html.escape(str(it.get("code", "?")))
    state = it.get("state", "")
    emoji, label, meaning = _STATE_INFO.get(state, ("•", str(state), ""))
    is_markup = state in _MARKUP_STATES

    # Header + subjudul (tanggal, + kekuatan sinyal hanya utk setup MARKUP).
    lines = [f"{emoji} <b>{code}</b> · {label}"]
    sub = _fmt_date_id(date)
    if is_markup:
        sub += f" · Kekuatan sinyal {it.get('confidence', 0)}/100"
    lines += [sub, ""]

    if meaning:
        lines += [f"<i>{html.escape(meaning)}</i>", ""]

    # Kondisi pasar + kekuatan relatif vs IHSG (bahasa awam).
    regime = it.get("regime")
    rs = it.get("relative_strength")
    if regime or rs is not None:
        seg = "📊 Pasar (IHSG): " + {
            "BULLISH": "sedang naik", "BEARISH": "sedang lemah",
        }.get(regime, str(regime or "-"))
        if rs is not None and rs > 0:
            seg += f" · {code} lebih kuat +{rs:.1%} dari pasar"
        elif rs is not None and rs < 0:
            seg += f" · {code} tertinggal {abs(rs):.1%} dari pasar"
        lines += [seg, ""]

    # Bacaan (narasi Claude / fallback rule-based).
    narr = it.get("narrative")
    if narr:
        lines += ["💡 <b>Bacaan</b>", html.escape(str(narr)), ""]

    # Rencana trading — MARKUP_* = rencana penuh; ACCUMULATION_ONGOING = panduan
    # level pantau (BOB/BOW/invalidasi), entry bersyarat — bukan sinyal masuk.
    lv = it.get("levels")
    if is_markup and lv:
        lines.append("🎯 <b>Rencana (kalau harga breakout — bukan harga sekarang)</b>")
        lines.append(f"• Beli di atas: <b>{lv['entry']:g}</b>")
        lines.append(f"• Batas rugi (SL): {lv['stop_loss']:g} (turun {lv['stop_pct']:.1%})")
        lines.append(
            f"• Target jual (TP): {lv['take_profit']:g} "
            f"(untung ±{lv['rr_realized']:.1f}× dari risiko yang dipertaruhkan)"
        )
        lines += [f"• Perkiraan tahan: ~{lv['est_hold_days']} hari", ""]
    elif lv:
        _, bow_hi = bow_zone(lv)
        lines.append("📐 <b>Panduan level selama pantau (belum sinyal masuk)</b>")
        lines.append(
            f"• BOB — beli saat breakout: tunggu tembus dan bertahan di atas "
            f"<b>{lv['entry']:g}</b>, idealnya dengan volume ramai"
        )
        lines.append(
            f"   ↳ kalau kejadian: SL {lv['stop_loss']:g} "
            f"(turun {lv['stop_pct']:.1%}) · TP {lv['take_profit']:g} "
            f"(R:R {lv['rr_realized']:.1f})"
        )
        lines.append(
            f"• BOW — nyicil di area lemah: kisaran {lv['support']:g}–{bow_hi:g} "
            f"dekat support (lebih agresif, wajib SL disiplin di bawah "
            f"{lv['support']:g})"
        )
        lines.append(
            f"• Setup batal: harga tutup di bawah <b>{lv['support']:g}</b> → "
            f"coret dari pantauan"
        )
        lines.append("")

    # Kenapa masuk radar (sinyal teknis diterjemahkan ke bahasa awam).
    bullets = _why_bullets(it.get("signals", {}), it.get("ownership"))
    if bullets:
        lines.append("🔍 <b>Kenapa masuk radar</b>")
        lines += [f"• {html.escape(b)}" for b in bullets]
        lines.append("")

    # Rotasi broker: interpretasi bila rotasi bullish, kalau tidak angka ringkas.
    rot = it.get("rotation")
    if rot:
        retail, foreign, smart = rot["retail_net"], rot["foreign_net"], rot["smart_net"]
        if retail < 0 and (foreign > 0 or smart > 0):
            lines.append("🔄 Ritel lepas barang, asing/smart money menampung "
                         "(rotasi ke tangan kuat)")
        else:
            lines.append(
                f"🔄 Aliran broker — ritel {fmt_rp(retail)} · "
                f"asing {fmt_rp(foreign)} · smart {fmt_rp(smart)}"
            )

    # Orang dalam (insider): akumulasi = konfirmasi, divestasi saat naik = red flag.
    ins = it.get("insider")
    if ins:
        net = ins.get("net_change_pct", 0.0)
        wd = ins.get("window_days", 30)
        n = ins.get("n_reports", 0)
        if net > 0:
            seg = f"👤 Orang dalam ({wd}h): beli bersih +{net:.1f} poin kepemilikan · {n} laporan ✅"
        elif net < 0:
            seg = f"👤 Orang dalam ({wd}h): jual bersih {net:.1f} poin kepemilikan · {n} laporan ⚠️"
        else:
            seg = f"👤 Orang dalam ({wd}h): {n} laporan (netral)"
        if ins.get("last_purpose"):
            seg += f" · terakhir: {html.escape(str(ins['last_purpose']))}"
        lines.append(seg)

    # Agenda korporasi mendatang (RUPS/dividen/dst).
    ca = it.get("corp_actions")
    if ca:
        lines.append("📅 Agenda: " + " · ".join(
            f"{html.escape(str(c['label']))} {html.escape(_fmt_date_id(str(c['date'])))}"
            for c in ca[:3]
        ))

    lines.append("")
    if is_markup:
        lines.append("⚠️ <i>Bukan ajakan beli/jual. Masuk hanya kalau breakout "
                     "terkonfirmasi, bukan di harga sekarang. Atur risiko & "
                     "ukuran posisi sendiri.</i>")
    else:
        lines.append("⚠️ <i>Bukan ajakan beli/jual. Ini baru tahap pantau — "
                     "belum ada sinyal masuk. Kelola risiko sendiri.</i>")
    return "\n".join(lines)


# --- Sinyal LIVE order book (dari live_watch): tag verdict -> bahasa awam ---
_LIVE_VERDICT_ID = {
    "FAKE_OVER": "Tembok jual ternyata palsu — bandar menahan harga sambil "
                 "menyerap barang (sinyal positif)",
    "DEMAND_REAL": "Antrian beli tebal dan asli — permintaan kuat (sinyal positif)",
    "DEMAND_DOMINAN": "Antrian beli jauh lebih dominan dibanding jual",
    "PASSIVE_ACCUM": "Ada yang diam-diam menampung barang di antrian beli",
    "SUPPLY_REAL": "Tembok jual tampak asli — tekanan jual nyata, hati-hati",
    "FAKE_BID": "Antrian beli tebal tapi palsu — waspada jebakan",
    "PASSIVE_DISTRIB": "Ada yang diam-diam melepas barang",
    "SEIMBANG": "Antrian beli dan jual relatif seimbang",
    "SUPPLY_DOMINAN": "Antrian jual lebih dominan",
    "RITEL_NOISE": "Ramai order ritel kecil, arah belum jelas",
    "NO_DATA": "Data antrian belum tersedia",
}
_LIVE_WALL_PULLED = ("Tembok jual tiba-tiba ditarik atau dimakan — "
                     "sering jadi pemicu harga jebol naik")
# Versi terpilah cabut-vs-dimakan (dibedakan via bar intraday di live_watch):
_LIVE_WALL_VERDICT = {
    "EATEN": "Tembok jual DIMAKAN pembeli — permintaan asli menyerap barang "
             "di level tembok. Ini timing entry klasik tape-reading",
    "PULLED": "Tembok jual DICABUT, bukan dimakan — penjual besar menarik "
              "ordernya (fake offer terkonfirmasi). Positif, tapi tunggu "
              "pembeli nyata muncul sebelum masuk",
}


def format_live_signal(
    code: str,
    *,
    verdict: str | None = None,
    wall_pulled: bool = False,
    wall_verdict: str | None = None,
    imb: float | None = None,
    accum_label: str = "",
    time_str: str = "",
) -> str:
    """Pesan Telegram ramah-awam untuk sinyal LIVE order book (dari live_watch).

    Dua pemicu: `wall_pulled` (tembok jual dicabut/dimakan saat akumulasi) atau
    transisi ke `verdict` bullish. `wall_verdict` ("EATEN"/"PULLED"/None) memilah
    penyebab susutnya tembok bila live_watch berhasil cek bar intraday; None =
    tak terbedakan -> pesan generik lama. Angka order book mentah (lot/order,
    tag verdict teknis) diterjemahkan ke bahasa sehari-hari.
    """
    safe = html.escape(str(code))
    if wall_pulled:
        reason = _LIVE_WALL_VERDICT.get(wall_verdict or "", _LIVE_WALL_PULLED)
    else:
        reason = _LIVE_VERDICT_ID.get(verdict or "", str(verdict or ""))

    head = f"🟢 <b>{safe}</b> · Sinyal Live"
    if time_str:
        head += f" · {html.escape(time_str)}"
    lines = [head, "", f"<i>{html.escape(reason)}</i>", ""]

    if imb is not None:
        if imb >= 1.05:
            lines.append(f"📊 Antrian beli {imb:.1f}× lebih tebal dari antrian jual")
        elif 0 < imb <= 0.95:
            lines.append(f"📊 Antrian jual {1 / imb:.1f}× lebih tebal dari antrian beli")
        else:
            lines.append("📊 Antrian beli dan jual seimbang")

    # Status akumulasi broker dari label ("AKUM/6d" / "no-akum" / "?").
    if accum_label.startswith("AKUM"):
        days = accum_label.split("/", 1)[1].rstrip("d") if "/" in accum_label else ""
        extra = f" ({days} hari beruntun)" if days else ""
        lines.append(f"🏦 Broker masih memborong{extra}")
    elif accum_label == "no-akum":
        lines.append("🏦 Broker belum terlihat memborong")

    lines.append("")
    lines.append("⚠️ <i>Pantauan real-time order book, bukan ajakan beli/jual. "
                 "Atur timing &amp; risiko sendiri.</i>")
    return "\n".join(lines)


def format_live_bow(
    code: str,
    *,
    kind: str,
    bow_lo: float,
    bow_hi: float,
    support: float,
    time_str: str = "",
) -> str:
    """Pesan live untuk monitor zona BOW pada saham pantau (ACCUMULATION_ONGOING).

    kind "AC"      : harga masuk zona BOW dua siklus berturut DAN antrian beli
                     dijaga/di-refill — konfirmasi ala tape-reading ("BOW after
                     confirmasi di bid nya di refil").
    kind "INVALID" : harga jatuh di bawah support — setup akumulasi batal.
    """
    safe = html.escape(str(code))
    when = f" · {html.escape(time_str)}" if time_str else ""
    if kind == "INVALID":
        return "\n".join([
            f"🔻 <b>{safe}</b> · Setup Batal{when}",
            "",
            f"<i>Harga jatuh di bawah support {support:g} — setup akumulasi "
            f"batal, coret dari pantauan.</i>",
            "",
            "⚠️ <i>Bukan ajakan beli/jual. Kelola risiko sendiri.</i>",
        ])
    return "\n".join([
        f"🟦 <b>{safe}</b> · Peluang BOW{when}",
        "",
        "<i>Harga masuk area beli-lemah (BOW) dan antrian beli terlihat "
        "dijaga/di-refill dua siklus berturut — konfirmasi ala tape-reading.</i>",
        "",
        f"📐 Zona BOW: {bow_lo:g}–{bow_hi:g}",
        f"• Kalau mau nyicil: wajib disiplin SL di bawah <b>{support:g}</b>",
        "",
        "⚠️ <i>Bukan ajakan beli/jual. Ini konfirmasi zona pantau — lebih agresif "
        "dari menunggu breakout. Atur ukuran posisi & risiko sendiri.</i>",
    ])


def send_telegram(token: str, chat_id: str, text: str, *, timeout: float = 15.0) -> bool:
    """Kirim pesan ke Telegram (parse_mode HTML). Return True bila sukses."""
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum di-set.")
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return True
