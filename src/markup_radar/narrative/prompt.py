"""Prompt + fallback narasi — dipakai bersama semua provider LLM.

Dipisah dari backend supaya ganti provider (Claude / NVIDIA NIM / lainnya)
tidak mengubah isi prompt. Kalau wording narasi mau diubah, ubah DI SINI saja
— semua provider ikut.
"""

from __future__ import annotations

SYSTEM_PROMPT = (
    "Kamu analis saham Indonesia yang menjelaskan kondisi saham ke investor "
    "PEMULA. Jawab HANYA dengan 2-3 kalimat Bahasa Indonesia yang mengalir, "
    "tanpa judul, tanpa bullet, tanpa tanda kutip, tanpa penjelasan proses "
    "berpikirmu. Jangan pernah memberi rekomendasi beli/jual eksplisit."
)


def build_prompt(code: str, signals: dict, extra_context: str = "") -> str:
    """Susun prompt user dari sinyal mentah satu emiten.

    `extra_context` (opsional): konteks insider/corporate action dari run_daily
    — disebut di narasi hanya bila relevan (mis. insider divestasi saat sinyal
    MARKUP = red flag; insider akumulasi = konfirmasi).
    """
    prompt = (
        f"Tulis 2-3 kalimat ringkas Bahasa Indonesia yang MUDAH dipahami tentang "
        f"saham {code}. "
        f"HINDARI istilah teknis (jangan sebut 'done-ratio', 'RVOL', 'close-in-range', "
        f"'MA50', kode state) — terjemahkan ke bahasa sehari-hari: seberapa agresif "
        f"pembeli, volume ramai/sepi, harga tutup kuat/lemah, broker masih borong "
        f"atau tidak, dan kondisi pasar. "
        f"Data mentah (untuk kamu terjemahkan, JANGAN dikutip apa adanya): "
        f"agresivitas beli={signals.get('done_ratio', 0.0):.2f} (>0.55 pembeli agresif, "
        f"<0.45 penjual dominan), volume={signals.get('rvol', 0.0):.1f}x rata-rata, "
        f"posisi tutup={signals.get('close_in_range', 0.0):.2f} (mendekati 1=tutup di puncak), "
        f"broker borong {signals.get('broker_net_buy_streak', 0)} hari beruntun, "
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
    return prompt


def fallback(code: str, state: str, signals: dict) -> str:
    """Ringkasan rule-based bahasa awam bila LLM tak tersedia/gagal.

    Ini yang tampil di alert selama provider mati — sengaja tetap layak baca,
    bukan pesan error, supaya sinyal tetap terkirim walau LLM down.
    """
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
