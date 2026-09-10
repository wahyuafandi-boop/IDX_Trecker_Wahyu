"""Narasi alert — multi-provider (NVIDIA NIM / Anthropic Claude).

Pilih lewat `narrative.provider` di config/settings.yaml. Apa pun providernya,
kegagalan TIDAK PERNAH menggagalkan run: narasi jatuh ke ringkasan rule-based
supaya sinyal tetap terkirim ke Telegram.
"""

from __future__ import annotations

import sys

from markup_radar.narrative import claude as _claude
from markup_radar.narrative import nvidia as _nvidia
from markup_radar.narrative.prompt import build_prompt, fallback

__all__ = ["generate_narrative", "build_prompt", "fallback"]


def generate_narrative(
    code: str,
    state: str,
    signals: dict,
    *,
    api_key: str = "",
    model: str = "",
    provider: str = "nvidia",
    fallback_models: list[str] | None = None,
    extra_context: str = "",
    verbose: bool = True,
    stats: dict | None = None,
) -> str:
    """Satu narasi bahasa awam untuk satu emiten.

    `api_key` harus milik provider yang dipilih (NVIDIA_API_KEY untuk "nvidia",
    ANTHROPIC_API_KEY untuk "claude"). Gagal apa pun -> ringkasan rule-based,
    dengan alasan dicetak ke stderr supaya kelihatan di log VPS (bukan diam —
    provider mati harus terbaca saat audit log, bukan cuma terasa dari kualitas
    narasi yang menurun).

    `stats` (opsional): dict yang diisi di tempat dengan hitungan `llm` /
    `fallback` / daftar `errors`. Dipakai run_daily untuk MENDETEKSI provider
    yang mati total. Sebelum ini kegagalan cuma jadi baris [WARN] per sinyal —
    dan memang terbukti tak terbaca: seluruh model NVIDIA di config EOL sejak
    7 Agustus 2026 (410 Gone) dan 20 run berjalan dengan narasi rule-based
    tanpa ada yang sadar sampai audit 9 September.
    """
    prov = (provider or "nvidia").strip().lower()
    try:
        if prov == "nvidia":
            out = _nvidia.generate(
                code, signals,
                api_key=api_key,
                model=model or "moonshotai/kimi-k3",
                fallback_models=fallback_models,
                extra_context=extra_context,
            )
        elif prov == "claude":
            out = _claude.generate(
                code, signals,
                api_key=api_key,
                model=model or "claude-opus-4-8",
                extra_context=extra_context,
            )
        elif prov in ("none", "off", "rule"):
            return fallback(code, state, signals)
        else:
            raise ValueError(f"provider tidak dikenal: {provider!r}")
    except Exception as exc:  # noqa: BLE001 — narasi opsional, run tetap jalan
        if verbose:
            print(f"[WARN] narasi {code} via {prov} gagal -> pakai rule-based: {exc}",
                  file=sys.stderr)
        if stats is not None:
            stats["fallback"] = stats.get("fallback", 0) + 1
            stats.setdefault("errors", []).append(f"{code}: {exc}")
        return fallback(code, state, signals)
    if stats is not None:
        stats["llm"] = stats.get("llm", 0) + 1
    return out
