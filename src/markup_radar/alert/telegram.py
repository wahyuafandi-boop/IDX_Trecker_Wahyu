"""Format & kirim watchlist harian ke Telegram (spec §6, Phase 4).

Pakai parse_mode HTML (bukan Markdown) supaya nama state yang mengandung
underscore (mis. MARKUP_START) tidak memecah parser Telegram.
"""

from __future__ import annotations

import html

import requests

from markup_radar.ingest.ownership_client import fmt_rp

_EMOJI = {
    "MARKUP_CONFIRMED": "✅",
    "MARKUP_START": "🚀",
    "ACCUMULATION_ONGOING": "🟡",
    "DISTRIBUTION_WARNING": "🔻",
}


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

        # Baris sinyal dasar.
        lines.append(
            f"   done {s.get('done_ratio', 0):.2f} · "
            f"RVOL {s.get('rvol', 0):.1f}x · "
            f"close {s.get('close_in_range', 0):.2f} · "
            f"streak {s.get('broker_net_buy_streak', 0)}"
        )

        # Baris level — HANYA untuk MARKUP_* (levels terisi; spec D5/§4.8). State lain
        # (ACCUMULATION/DISTRIBUTION) -> levels None -> tampil tanpa entry.
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
