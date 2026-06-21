"""Format & kirim watchlist harian ke Telegram (spec §6, Phase 4).

Pakai parse_mode HTML (bukan Markdown) supaya nama state yang mengandung
underscore (mis. MARKUP_START) tidak memecah parser Telegram.
"""

from __future__ import annotations

import html

import requests

_EMOJI = {
    "MARKUP_START": "🚀",
    "ACCUMULATION_ONGOING": "🟡",
    "DISTRIBUTION_WARNING": "🔻",
}


def format_alert(date: str, items: list[dict]) -> str:
    """items: list of {code, state, confidence, signals}. -> string HTML."""
    if not items:
        return f"<b>Markup Radar</b> — {html.escape(date)}\nTidak ada sinyal actionable hari ini."

    lines = [f"<b>Markup Radar</b> — {html.escape(date)}", ""]
    # Urutkan: MARKUP_START dulu, lalu confidence tertinggi.
    order = {"MARKUP_START": 0, "ACCUMULATION_ONGOING": 1, "DISTRIBUTION_WARNING": 2}
    items = sorted(items, key=lambda x: (order.get(x["state"], 9), -x.get("confidence", 0)))

    for it in items:
        s = it.get("signals", {})
        emoji = _EMOJI.get(it["state"], "•")
        lines.append(
            f"{emoji} <b>{html.escape(str(it['code']))}</b> — "
            f"{html.escape(str(it['state']))} ({it.get('confidence', 0)})\n"
            f"   done {s.get('done_ratio', 0):.2f} · "
            f"RVOL {s.get('rvol', 0):.1f}x · "
            f"close {s.get('close_in_range', 0):.2f} · "
            f"broker streak {s.get('broker_net_buy_streak', 0)}"
        )
        if it.get("narrative"):
            lines.append(f"   <i>{html.escape(str(it['narrative']))}</i>")
    lines.append("")
    lines.append("<i>Setup swing 2–4 minggu (horizon 10–20 hari), bukan entry "
                 "intraday. Konfirmasi & kelola risiko sendiri.</i>")
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
