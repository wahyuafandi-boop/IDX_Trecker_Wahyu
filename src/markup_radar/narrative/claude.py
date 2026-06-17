"""Generate narasi singkat per alert via Anthropic Claude API (opsional).

Contoh output:
  "BBRI: done-offer 0.68, broker net buy 5 hari, RVOL 2.3x, close kuat ->
   indikasi markup mulai. IHSG di atas MA50."
"""

from __future__ import annotations


def generate_narrative(
    code: str,
    state: str,
    signals: dict,
    *,
    api_key: str,
    model: str = "claude-opus-4-8",
) -> str:
    """Hasilkan satu kalimat narasi. Fallback ke ringkasan rule-based bila SDK
    anthropic tidak terpasang atau API key kosong."""
    if not api_key:
        return _fallback(code, state, signals)
    try:
        import anthropic
    except ImportError:
        return _fallback(code, state, signals)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        f"Buat satu kalimat ringkas (Bahasa Indonesia) untuk alert swing trading. "
        f"Saham {code}, state {state}. Sinyal: "
        f"done_ratio={signals.get('done_ratio'):.2f}, "
        f"rvol={signals.get('rvol'):.1f}x, "
        f"close_in_range={signals.get('close_in_range'):.2f}, "
        f"broker_net_buy_streak={signals.get('broker_net_buy_streak')}, "
        f"ihsg_above_ma50={signals.get('ihsg_above_ma50')}. "
        f"Jangan beri rekomendasi beli/jual eksplisit, cukup deskripsi kondisi."
    )
    msg = client.messages.create(
        model=model,
        max_tokens=120,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _fallback(code: str, state: str, signals: dict) -> str:
    s = signals
    return (
        f"{code}: done-ratio {s.get('done_ratio', 0):.2f}, "
        f"broker net buy {s.get('broker_net_buy_streak', 0)} hari, "
        f"RVOL {s.get('rvol', 0):.1f}x -> {state}."
    )
