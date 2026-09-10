"""Adu model NVIDIA NIM untuk narasi alert — pilih pemenang pakai data, bukan feeling.

Kenapa ada: "model mana yang bagus" tidak bisa dijawab dari spek atau leaderboard.
Yang menentukan di sini sempit dan spesifik — Bahasa Indonesia yang enak dibaca
investor pemula, patuh larangan istilah teknis, tidak bocor blok reasoning, dan
tidak memberi rekomendasi beli/jual. Skrip ini menjalankan kandidat pada sinyal
ASLI (run 20 Juli 2026) lalu mencetak hasil + pelanggaran otomatis, berdampingan.

Pakai:
    python scripts/bench_narrative.py                    # semua kandidat default
    python scripts/bench_narrative.py --models a,b,c     # kandidat pilihan
    python scripts/bench_narrative.py --fixture MIKA     # satu kasus saja
    python scripts/bench_narrative.py --runs 2           # cek konsistensi

Butuh NVIDIA_API_KEY di .env (gratis: build.nvidia.com -> Get API Key).
"""

from __future__ import annotations

import argparse
import os
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.narrative import nvidia  # noqa: E402

# Kandidat: model teks terkuat di katalog NIM per Juli 2026, dipilih dengan
# preferensi ke yang multilingual-nya kuat (Qwen/DeepSeek/GLM historisnya paling
# rapi di Bahasa Indonesia) + satu wakil Nemotron (tuan rumah, latensi enak).
DEFAULT_MODELS = [
    "deepseek-ai/deepseek-v4-pro",
    "deepseek-ai/deepseek-v4-flash",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3-nano-30b-a3b",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "meta/llama-4-maverick-17b-128e-instruct",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "bytedance/seed-oss-36b-instruct",
    "google/gemma-4-31b-it",
]

# Diprobe 2026-07-23 dan TIDAK dilayani di /v1/chat/completions walau terdaftar
# di /v1/models — jangan dimasukkan lagi tanpa probe ulang:
#   qwen/qwen3.5-397b-a17b   404 "Specified function is not available"
#   moonshotai/kimi-k2.6     404 "Not found for account"
#   nvidia/nemotron-nano-3-30b-a3b  404 "Model not found"
# Sering timeout (cold-start, bukan mati — gemma-4-31b terbukti 200 saat lengang):
#   qwen/qwen3-next-80b, z-ai/glm-5.2, mistralai/mistral-large-3, mistral-medium-3.5

# Sinyal ASLI dari logs/eod_20260720.log (run sukses terakhir sebelum token mati).
# Sengaja dipilih tiga karakter berbeda supaya kelemahan model kelihatan:
FIXTURES: dict[str, dict] = {
    # Volume meledak, pembeli sedikit unggul, tutup kuat — kasus "mudah".
    "PACK": {
        "state": "MARKUP_EARLY",
        "signals": {"done_ratio": 0.53, "rvol": 4.1, "close_in_range": 0.86,
                    "broker_net_buy_streak": 4, "ihsg_above_ma50": True},
        "extra_context": "",
    },
    # Volume sepi tapi tutup di puncak, broker tidak borong — kontradiktif.
    # Model lemah biasanya menyimpulkan terlalu bullish di sini.
    "MIKA": {
        "state": "DISTRIBUTION_WARNING",
        "signals": {"done_ratio": 0.39, "rvol": 0.8, "close_in_range": 1.00,
                    "broker_net_buy_streak": 0, "ihsg_above_ma50": True},
        "extra_context": "",
    },
    # Penjual dominan + tutup lemah TAPI broker akumulasi 4 hari, pasar lemah,
    # plus konteks insider. Kasus tersulit: butuh nuansa, bukan template.
    "DEWA": {
        "state": "ACCUMULATION",
        "signals": {"done_ratio": 0.40, "rvol": 2.7, "close_in_range": 0.33,
                    "broker_net_buy_streak": 4, "ihsg_above_ma50": False},
        "extra_context": "direktur menjual 12 juta lembar pekan lalu; "
                         "porsi ritel turun 0.4pp dalam 5 bulan",
    },
}

# --- Pemeriksaan otomatis: yang bisa dinilai mesin, dinilai mesin ------------
JARGON = re.compile(
    r"\b(done[- ]?ratio|done[- ]?offer|rvol|close[- ]in[- ]range|ma\s?50|"
    r"relative strength|markup|accumulation|distribution_warning|conf\b)",
    re.IGNORECASE,
)
RECO = re.compile(
    r"\b(rekomendasi|saya sarankan|sebaiknya (beli|jual)|layak (beli|dibeli)|"
    r"buy now|sell now|target harga)\b",
    re.IGNORECASE,
)
THINK = re.compile(r"</?think>|^(oke|baik),? (saya|mari) (akan )?(analisis|pikirkan)",
                   re.IGNORECASE)
# Kata fungsi Bahasa Indonesia — kalau nyaris tak ada, model kemungkinan
# menjawab dalam Inggris/campur.
ID_WORDS = re.compile(r"\b(yang|dan|ini|itu|dengan|masih|belum|sedang|harga|"
                      r"pembeli|penjual|saham|pasar)\b", re.IGNORECASE)


def audit(text: str, signals: dict) -> list[str]:
    """Kembalikan daftar pelanggaran. Kosong = bersih."""
    bad: list[str] = []
    if not text.strip():
        return ["KOSONG"]
    if THINK.search(text):
        bad.append("bocor-reasoning")
    if JARGON.search(text):
        bad.append("jargon-teknis")
    if RECO.search(text):
        bad.append("rekomendasi-eksplisit")
    if len(ID_WORDS.findall(text)) < 3:
        bad.append("bukan-bahasa-indonesia")
    # Angka mentah dikutip apa adanya (prompt melarang).
    for key in ("done_ratio", "rvol", "close_in_range"):
        val = signals.get(key)
        if val is not None and f"{val:.2f}" in text:
            bad.append(f"kutip-angka:{key}")
    kalimat = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    if not 2 <= len(kalimat) <= 4:
        bad.append(f"jumlah-kalimat:{len(kalimat)}")
    if len(text) > 600:
        bad.append(f"kepanjangan:{len(text)}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", help="daftar model dipisah koma (default: kandidat bawaan)")
    ap.add_argument("--fixture", help="jalankan satu kasus saja (PACK/MIKA/DEWA)")
    ap.add_argument("--runs", type=int, default=1, help="ulangi tiap kasus N kali")
    # 120s bukan berlebihan: model NIM yang jarang dipakai bisa cold-start lama
    # (deepseek-v4-flash tembus 90s saat probe). Timeout pendek = salah menuduh
    # model lambat sebagai model gagal.
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    # Konsol Windows default cp1252 dan LANGSUNG CRASH begitu model mengeluarkan
    # karakter di luar Latin-1 (mis. non-breaking hyphen U+2011 dari gpt-oss) —
    # bench sempat mati di tengah jalan karenanya. Paksa UTF-8, dan kalau
    # terminalnya tetap tak sanggup, ganti karakter daripada menggugurkan run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    api_key = os.getenv("NVIDIA_API_KEY", "")
    if not api_key:
        print("[ERROR] NVIDIA_API_KEY belum di-set di .env.\n"
              "        Ambil gratis: https://build.nvidia.com -> login -> Get API Key\n"
              "        Lalu tambahkan baris:  NVIDIA_API_KEY=nvapi-xxxxxxxx",
              file=sys.stderr)
        return 1

    models = ([m.strip() for m in args.models.split(",") if m.strip()]
              if args.models else DEFAULT_MODELS)
    cases = ({args.fixture.upper(): FIXTURES[args.fixture.upper()]}
             if args.fixture else FIXTURES)

    skor: dict[str, dict] = {}

    for model in models:
        print("\n" + "=" * 78)
        print(f"MODEL: {model}")
        print("=" * 78)
        lat: list[float] = []
        pelanggaran = 0
        gagal = 0

        for code, fx in cases.items():
            for run in range(args.runs):
                t0 = time.time()
                try:
                    out = nvidia.generate(
                        code, fx["signals"],
                        api_key=api_key,
                        model=model,
                        fallback_models=[],      # jangan failover: kita menilai model INI
                        extra_context=fx["extra_context"],
                        timeout=args.timeout,
                        max_retries=2,
                    )
                except Exception as exc:  # noqa: BLE001
                    gagal += 1
                    print(f"\n[{code}] GAGAL: {str(exc)[:200]}")
                    continue

                dt = time.time() - t0
                lat.append(dt)
                bad = audit(out, fx["signals"])
                pelanggaran += len(bad)
                tag = "OK" if not bad else "LANGGAR: " + ", ".join(bad)
                suffix = f" run{run + 1}" if args.runs > 1 else ""
                print(f"\n[{code}{suffix}] {dt:.1f}s  {tag}")
                print(f"  {out}")

        skor[model] = {
            "latensi": statistics.median(lat) if lat else None,
            "pelanggaran": pelanggaran,
            "gagal": gagal,
            "berhasil": len(lat),
        }

    print("\n\n" + "=" * 78)
    print("RINGKASAN  (pelanggaran makin kecil makin baik)")
    print("=" * 78)
    print(f"{'model':<46} {'ok':>4} {'gagal':>6} {'langgar':>8} {'latensi':>9}")
    print("-" * 78)
    urut = sorted(skor.items(),
                  key=lambda kv: (kv[1]["gagal"], kv[1]["pelanggaran"],
                                  kv[1]["latensi"] or 999))
    for model, s in urut:
        lat_s = f"{s['latensi']:.1f}s" if s["latensi"] else "-"
        print(f"{model:<46} {s['berhasil']:>4} {s['gagal']:>6} "
              f"{s['pelanggaran']:>8} {lat_s:>9}")

    if urut and urut[0][1]["berhasil"]:
        juara = urut[0][0]
        print(f"\nTeratas otomatis: {juara}")
        print("Pemeriksaan mesin cuma menyaring yang jelas salah — BACA output di atas "
              "dan pilih yang bahasanya paling enak. Lalu set di config/settings.yaml:")
        print(f"  narrative:\n    provider: nvidia\n    model: {juara}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
