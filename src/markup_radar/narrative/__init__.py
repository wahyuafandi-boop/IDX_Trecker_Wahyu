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
) -> str:
    """Satu narasi bahasa awam untuk satu emiten.

    `api_key` harus milik provider yang dipilih (NVIDIA_API_KEY untuk "nvidia",
    ANTHROPIC_API_KEY untuk "claude"). Gagal apa pun -> ringkasan rule-based,
    dengan alasan dicetak ke stderr supaya kelihatan di log VPS (bukan diam —
    provider mati harus terbaca saat audit log, bukan cuma terasa dari kualitas
    narasi yang menurun).
    """
    prov = (provider or "nvidia").strip().lower()
    try:
        if prov == "nvidia":
            return _nvidia.generate(
                code, signals,
                api_key=api_key,
                model=model or "qwen/qwen3.5-397b-a17b",
                fallback_models=fallback_models,
                extra_context=extra_context,
            )
        if prov == "claude":
            return _claude.generate(
                code, signals,
                api_key=api_key,
                model=model or "claude-opus-4-8",
                extra_context=extra_context,
            )
        if prov in ("none", "off", "rule"):
            return fallback(code, state, signals)
        raise ValueError(f"provider tidak dikenal: {provider!r}")
    except Exception as exc:  # noqa: BLE001 — narasi opsional, run tetap jalan
        if verbose:
            print(f"[WARN] narasi {code} via {prov} gagal -> pakai rule-based: {exc}",
                  file=sys.stderr)
        return fallback(code, state, signals)
