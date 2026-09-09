#!/usr/bin/env python3
"""Probe model NVIDIA NIM mana yang BENAR-BENAR melayani /v1/chat/completions.

Kenapa ada: katalog `/v1/models` TIDAK bisa dipercaya. Model bisa (a) terdaftar
tapi 404 di chat/completions, (b) sudah EOL dan balas 410 Gone, atau (c) hidup
tapi timeout permanen. Audit 2026-09-09 menemukan seluruh model di settings.yaml
kena 410 sejak 7 Agustus — narasi jatuh ke rule-based selama 20 run tanpa
terdeteksi. Jalankan skrip ini saat run_daily melapor mayoritas narasi fallback,
lalu perbarui blok `narrative` di config/settings.yaml dgn yang lolos.

    python scripts/probe_narrative_models.py                # kandidat default
    python scripts/probe_narrative_models.py --all          # semua model katalog
    python scripts/probe_narrative_models.py --models a b   # daftar sendiri
    python scripts/probe_narrative_models.py --timeout 90

Diuji dgn prompt narasi ASLI + fixture jebakan (pembeli dominan, broker net BELI,
close di puncak range) supaya model yang membalik fakta ikut ketahuan, bukan cuma
yang mati.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.config import load_settings
from markup_radar.narrative import nvidia as nv

# Fixture jebakan — semua angka saling menguatkan ke arah BULLISH. Model yang
# menulis "penjual dominan" atau "broker melepas" di sini sudah gagal fidelitas.
FIXTURE = {
    "code": "PACK",
    "done_ratio": 0.6667,          # > 0.55 = PEMBELI dominan
    "rvol": 4.42,                  # volume 4.4x rata-rata
    "close_in_range": 1.0,         # close di PUNCAK range harian
    "broker_net_buy_streak": 4,    # broker net BELI 4 hari beruntun
    "queue_imbalance": 0.0,
    "relative_strength": 0.69,
    "flow_price_corr": 0.682,
    "near_range_high": True,
    "price_ranging": False,
    "absorption_flag": False,
    "ihsg_above_ma50": True,
}

DEFAULT_CANDIDATES = [
    "moonshotai/kimi-k3",
    "openai/gpt-oss-20b",
    "deepseek-ai/deepseek-v4-pro-0813",
    "deepseek-ai/deepseek-v4-flash-0731",
    "google/gemma-4-31b-it",
    "mistralai/mistral-nemotron",
    "nvidia/llama-3.1-nemotron-51b-instruct",
    "meta/llama-3.2-90b-vision-instruct",
]

# Frasa yang menandakan model membalik fakta fixture (fidelitas gagal).
RED_FLAGS = ("penjual dominan", "penjual lebih", "tekanan jual", "broker jual",
             "broker melepas", "melepas barang", "dekat dasar", "di titik terendah")


def catalog(key: str) -> list[str]:
    req = urllib.request.Request(f"{nv.BASE_URL}/models",
                                 headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as fh:
        return sorted(m["id"] for m in json.load(fh).get("data", []))


def classify_error(msg: str) -> str:
    if "410" in msg:
        return "410-EOL"
    if "404" in msg:
        return "404"
    if "timeout" in msg.lower():
        return "TIMEOUT"
    if "429" in msg:
        return "429"
    return "ERROR"


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe model narasi NVIDIA NIM")
    ap.add_argument("--models", nargs="+", metavar="ID", help="daftar model sendiri")
    ap.add_argument("--all", action="store_true", help="probe SEMUA model katalog (lambat)")
    ap.add_argument("--timeout", type=float, default=60.0, help="detik per model")
    ap.add_argument("--pace", type=float, default=1.5, help="jeda antar panggilan")
    args = ap.parse_args()

    # Narasi mengandung karakter non-ASCII (mis. non-breaking hyphen); console
    # Windows cp1252 akan meledak tanpa ini.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    key = load_settings().narrative_key("nvidia")
    if not key:
        print("[ERROR] NVIDIA_API_KEY kosong di .env", file=sys.stderr)
        return 1

    if args.models:
        models = args.models
    elif args.all:
        models = catalog(key)
        print(f"[info] {len(models)} model di katalog — ini akan lama.", file=sys.stderr)
    else:
        models = DEFAULT_CANDIDATES

    hidup: list[tuple[str, float, str]] = []
    for m in models:
        t0 = time.time()
        try:
            out = nv.generate("PACK", FIXTURE, api_key=key, model=m,
                              fallback_models=[], timeout=args.timeout, max_retries=1)
            dt = time.time() - t0
            flags = [f for f in RED_FLAGS if f in out.lower()]
            tag = "OK" if not flags else "FAKTA-SALAH"
            hidup.append((m, dt, out))
            print(f"[{tag:11s} {dt:>6.1f}s] {m}"
                  + (f"  <- {', '.join(flags)}" if flags else ""))
        except Exception as exc:  # noqa: BLE001 — probe: semua kegagalan menarik
            dt = time.time() - t0
            print(f"[{classify_error(str(exc)):11s} {dt:>6.1f}s] {m}  {str(exc)[:90]}")
        time.sleep(args.pace)

    print("\n" + "=" * 100)
    print(f"HIDUP: {len(hidup)}/{len(models)}")
    for m, dt, out in sorted(hidup, key=lambda x: x[1]):
        print(f"\n--- {m}  ({dt:.1f}s) ---\n{out}")
    if hidup:
        print("\nSalin ke config/settings.yaml blok `narrative`:")
        print(f"  model: {hidup[0][0]}")
        print("  fallback_models:")
        for m, _, _ in hidup[1:3]:
            print(f"    - {m}")
    return 0 if hidup else 1


if __name__ == "__main__":
    raise SystemExit(main())
