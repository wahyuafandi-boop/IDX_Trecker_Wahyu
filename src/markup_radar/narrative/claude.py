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
    extra_context: str = "",
) -> str:
    """Hasilkan satu kalimat narasi. Fallback ke ringkasan rule-based bila SDK
    anthropic tidak terpasang atau API key kosong.

    `extra_context` (opsional): konteks insider/corporate action dari
    run_daily — disebut di narasi hanya bila relevan (mis. insider divestasi
    saat sinyal MARKUP = red flag; insider akumulasi = konfirmasi).
    """
    if not api_key:
        return _fallback(code, state, signals)
    try:
        import anthropic
    except ImportError:
        return _fallback(code, state, signals)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        f"Kamu analis saham yang menjelaskan ke investor PEMULA. Tulis 2-3 kalimat "
        f"ringkas Bahasa Indonesia yang MUDAH dipahami tentang saham {code}. "
        f"HINDARI istilah teknis (jangan sebut 'done-ratio', 'RVOL', 'close-in-range', "
        f"'MA50', kode state) — terjemahkan ke bahasa sehari-hari: seberapa agresif "
        f"pembeli, volume ramai/sepi, harga tutup kuat/lemah, broker masih borong "
        f"atau tidak, dan kondisi pasar. "
        f"Data mentah (untuk kamu terjemahkan, JANGAN dikutip apa adanya): "
        f"agresivitas beli={signals.get('done_ratio'):.2f} (>0.55 pembeli agresif, "
        f"<0.45 penjual dominan), volume={signals.get('rvol'):.1f}x rata-rata, "
        f"posisi tutup={signals.get('close_in_range'):.2f} (mendekati 1=tutup di puncak), "
        f"broker borong {signals.get('broker_net_buy_streak')} hari beruntun, "
        f"pasar {'naik' if signals.get('ihsg_above_ma50') else 'lemah'}. "
        f"Jangan beri rekomendasi beli/jual eksplisit — cukup jelaskan apa yang "
        f"sedang terjadi dan artinya buat trader."
    )
    if extra_context:
        prompt += (
            f" Konteks tambahan (sebut HANYA bila penting, dan tetap bahasa awam, "
            f"mis. orang dalam jual saat harga mau naik = tanda bahaya, orang dalam "
            f"beli = konfirmasi, ada RUPS/dividen dekat, porsi ritel kecil/menyusut = "
            f"barang terkunci di tangan kuat, ritel jual sementara asing/smart money "
            f"menampung = tanda bagus): {extra_context}."
        )
    msg = client.messages.create(
        model=model,
        max_tokens=220,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _fallback(code: str, state: str, signals: dict) -> str:
    """Ringkasan rule-based bahasa awam bila API Claude tak tersedia."""
    s = signals
    dr = s.get("done_ratio", 0.5)
    sisi = ("pembeli agresif menyerap barang" if dr >= 0.55
            else "penjual masih lebih aktif" if dr <= 0.45
            else "tekanan beli-jual seimbang")
    streak = s.get("broker_net_buy_streak", 0)
    borong = f", broker borong {streak} hari beruntun" if streak >= 1 else ""
    return (
        f"{code}: {sisi}, volume {s.get('rvol', 0):.1f}× rata-rata{borong}. "
        f"Kondisi mengarah ke setup yang perlu dipantau."
    )
