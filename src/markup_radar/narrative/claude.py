"""Backend narasi via Anthropic Claude API.

Prompt-nya dipegang bersama di `narrative/prompt.py` — file ini murni transport
ke Anthropic. Dipertahankan sebagai jalur balik: `narrative.provider` di
settings.yaml boleh dikembalikan ke "claude" kapan saja tanpa ubah kode.
"""

from __future__ import annotations

from markup_radar.narrative.prompt import build_prompt


class ClaudeError(RuntimeError):
    """SDK tak terpasang, key kosong, atau panggilan API gagal."""


def generate(
    code: str,
    signals: dict,
    *,
    api_key: str,
    model: str = "claude-opus-4-8",
    extra_context: str = "",
    max_tokens: int = 220,
) -> str:
    """Hasilkan satu narasi singkat. Lempar ClaudeError bila tak bisa."""
    if not api_key:
        raise ClaudeError("ANTHROPIC_API_KEY belum di-set.")
    try:
        import anthropic
    except ImportError as exc:
        raise ClaudeError("SDK 'anthropic' belum terpasang.") from exc

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": build_prompt(code, signals, extra_context)}],
    )
    return msg.content[0].text.strip()
