"""Config loader: gabungkan settings.yaml + variabel environment (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:  # python-dotenv opsional saat runtime; di-skip kalau tidak ada.
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS = ROOT / "config" / "settings.yaml"


def parse_codes(tokens: list[str]) -> list[str]:
    """Normalisasi daftar kode saham dari argumen CLI (--codes).

    Terima campuran pemisah koma/spasi, mis. ``["BBCA,BBRI", "BMRI"]`` atau
    ``["bbca", "bbri"]``. Hasil: UPPERCASE, tanpa duplikat, urutan dipertahankan.
    """
    out: list[str] = []
    for token in tokens:
        for code in token.replace(",", " ").split():
            code = code.upper()
            if code and code not in out:
                out.append(code)
    return out


@dataclass
class Settings:
    """Konfigurasi engine yang sudah di-resolve (yaml + env)."""

    raw: dict[str, Any] = field(default_factory=dict)

    # --- Kredensial / env ---
    invezgo_api_key: str = ""
    invezgo_base_url: str = "https://api.invezgo.com"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    anthropic_api_key: str = ""
    db_path: str = "data/markup_radar.db"

    @property
    def watchlist(self) -> list[str]:
        return list(self.raw.get("watchlist", []))

    @property
    def windows(self) -> dict[str, int]:
        return dict(self.raw.get("windows", {}))

    @property
    def thresholds(self) -> dict[str, float]:
        return dict(self.raw.get("thresholds", {}))

    @property
    def score_weights(self) -> dict[str, float]:
        return dict(self.raw.get("score_weights", {}))

    @property
    def broker_top_n(self) -> int:
        return int(self.raw.get("broker", {}).get("top_n", 5))

    @property
    def rate_limit_per_min(self) -> int:
        return int(self.raw.get("api", {}).get("rate_limit_per_min", 250))

    @property
    def alert_states(self) -> list[str]:
        return list(self.raw.get("alert_states", []))

    @property
    def narrative(self) -> dict[str, Any]:
        return dict(self.raw.get("narrative", {}))

    @property
    def sheets(self) -> dict[str, Any]:
        return dict(self.raw.get("sheets", {}))


def load_settings(path: str | Path = DEFAULT_SETTINGS) -> Settings:
    """Baca settings.yaml lalu overlay kredensial dari environment."""
    load_dotenv(ROOT / ".env")

    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    return Settings(
        raw=raw,
        invezgo_api_key=os.getenv("INVEZGO_API_KEY", ""),
        # env var kosong (mis. secret CI tak di-set) -> pakai default, bukan "".
        invezgo_base_url=os.getenv("INVEZGO_BASE_URL") or "https://api.invezgo.com",
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        # chat_id: env menimpa default di settings.yaml (token tetap env-only).
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID")
        or str(raw.get("telegram", {}).get("chat_id", "")),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        db_path=os.getenv("MARKUP_RADAR_DB", "data/markup_radar.db"),
    )
