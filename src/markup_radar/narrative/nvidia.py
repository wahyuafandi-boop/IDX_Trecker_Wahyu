"""Narasi via NVIDIA NIM (build.nvidia.com) — endpoint OpenAI-compatible.

Kenapa NIM: gratis untuk anggota NVIDIA Developer Program, 100+ model termasuk
kelas frontier (DeepSeek V4, Qwen3.5, Kimi K2.6, GLM 5.2, Nemotron 3). Tidak
perlu kartu kredit. Cukup untuk beban kita: ~8 sinyal actionable per run, 1x
sehari.

Auth  : header `Authorization: Bearer <NVIDIA_API_KEY>` (key format `nvapi-...`)
Base  : https://integrate.api.nvidia.com/v1

CATATAN RATE LIMIT: 40 RPM itu *best-effort*, bukan jaminan — NVIDIA melempar
429 saat trafik global padat dan tidak melayani permintaan naik kuota. Jadi
penanganannya di sisi klien: backoff eksponensial + jitter, lalu failover ke
model cadangan. Kalau semua gagal, caller jatuh ke narasi rule-based
(prompt.fallback) supaya alert TETAP terkirim — LLM mati tidak boleh bikin
sinyal hilang.
"""

from __future__ import annotations

import random
import re
import time

import requests

from markup_radar.narrative.prompt import SYSTEM_PROMPT, build_prompt

BASE_URL = "https://integrate.api.nvidia.com/v1"

# Model reasoning (Nemotron, DeepSeek, gpt-oss, Qwen thinking) membungkus proses
# berpikir di <think>...</think> atau kanal terpisah. Narasi kita cuma butuh
# jawaban akhir — buang blok itu sebelum dipakai.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_ORPHAN_THINK_RE = re.compile(r"^.*?</think>", re.DOTALL | re.IGNORECASE)


class NvidiaError(RuntimeError):
    """Semua percobaan (termasuk failover) gagal."""


class NvidiaPermanentError(NvidiaError):
    """Gagal yang tidak akan pulih dengan menunggu — jangan di-retry.

    Contoh nyata: katalog /v1/models memuat model yang ternyata 404 di
    /v1/chat/completions. Mengulangnya 3x cuma membuang waktu dan menunda
    failover ke model cadangan yang sebenarnya hidup.
    """


def _clean(text: str) -> str:
    """Buang jejak reasoning + hiasan markdown, sisakan narasi bersih."""
    text = _THINK_RE.sub("", text)
    # </think> tanpa pembuka: terjadi saat model menaruh reasoning di luar tag
    # atau saat respons terpotong di tengah blok.
    if "</think>" in text:
        text = _ORPHAN_THINK_RE.sub("", text)
    text = text.strip()
    # Sebagian model membungkus jawaban dalam kutip atau blok kode.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text).strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def _extract(payload: dict) -> str:
    """Ambil teks jawaban dari respons chat-completions.

    Model reasoning NIM kadang menaruh isi di `reasoning_content` dan
    mengosongkan `content` bila max_tokens habis duluan — itu dianggap gagal
    supaya failover jalan, bukan mengirim narasi kosong ke Telegram.
    """
    choices = payload.get("choices") or []
    if not choices:
        raise NvidiaError("respons tanpa choices")
    msg = choices[0].get("message") or {}
    return _clean(msg.get("content") or "")


def _call_once(
    session: requests.Session,
    model: str,
    prompt: str,
    *,
    max_tokens: int,
    temperature: float,
    timeout: float,
    disable_thinking: bool,
) -> str:
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": 0.95,
        "stream": False,
    }
    if disable_thinking:
        # Dipahami keluarga Nemotron/Qwen di NIM; model yang tak kenal field ini
        # mengabaikannya (bukan error).
        body["chat_template_kwargs"] = {"thinking": False}

    resp = session.post(f"{BASE_URL}/chat/completions", json=body, timeout=timeout)
    if resp.status_code == 429:
        raise NvidiaError(f"rate limited (429) di {model}")   # transien -> retry
    if resp.status_code >= 500:
        raise NvidiaError(f"server error {resp.status_code} di {model}")  # transien
    if 400 <= resp.status_code < 500:
        # 404 = model tak dilayani di endpoint ini, 401 = key salah,
        # 400 = body ditolak. Tak satu pun pulih dengan menunggu.
        raise NvidiaPermanentError(
            f"{resp.status_code} di {model}: {resp.text[:160]}")
    text = _extract(resp.json())
    if not text:
        raise NvidiaError(f"{model} mengembalikan teks kosong")
    return text


def generate(
    code: str,
    signals: dict,
    *,
    api_key: str,
    model: str,
    fallback_models: list[str] | None = None,
    extra_context: str = "",
    max_tokens: int = 400,
    temperature: float = 0.6,
    # 45s: deepseek-v4-pro normal menjawab 7-24s; kalau tak sanggup dalam 45s
    # berarti endpoint-nya sedang overload (jam sibuk AS) -> lebih baik failover
    # ke model cadangan daripada menunggu 90s per panggilan. Digabung dgn
    # fast-failover-on-timeout, satu primary mati tak lagi bikin run molor.
    timeout: float = 45.0,
    max_retries: int = 2,
    disable_thinking: bool = True,
    session: requests.Session | None = None,
) -> str:
    """Hasilkan narasi via NIM. Lempar NvidiaError bila semua model gagal.

    Urutan coba: `model` dulu, lalu tiap entri `fallback_models`. Kebijakan ulang
    per model dibedakan menurut jenis gagal supaya failover tidak boros waktu:
      - 429 / 5xx / teks kosong : transien cepat -> retry (backoff+jitter)
      - TIMEOUT                 : model lambat/overload -> LANGSUNG failover,
        JANGAN ulang model yang sama (3x90s = 4.5 menit terbuang per model).
      - 4xx lain (404/401/400)  : permanen -> langsung failover.
    """
    if not api_key:
        raise NvidiaError("NVIDIA_API_KEY belum di-set (lihat config/.env.example).")

    prompt = build_prompt(code, signals, extra_context)
    sess = session or requests.Session()
    sess.headers.update(
        {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
    )

    errors: list[str] = []
    for candidate in [model, *(fallback_models or [])]:
        for attempt in range(max_retries):
            try:
                return _call_once(
                    sess, candidate, prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                    disable_thinking=disable_thinking,
                )
            except NvidiaPermanentError as exc:
                # Langsung lompat ke model cadangan — mengulang 404 itu sia-sia.
                errors.append(f"{candidate}: {exc}")
                break
            except requests.exceptions.Timeout as exc:
                # Model lambat/overload. Mengulang model YANG SAMA pada timeout
                # 90s = buang 90s lagi tiap percobaan sebelum akhirnya failover.
                # Lebih baik segera coba model berikut yang mungkin sehat.
                errors.append(f"{candidate}: timeout {timeout:.0f}s -> failover")
                break
            except Exception as exc:  # noqa: BLE001 — 429/5xx/kosong: retry cepat
                errors.append(f"{candidate} (percobaan {attempt + 1}): {exc}")
                if attempt < max_retries - 1:
                    # 1s, 2s + jitter — jitter mencegah semua sinyal dalam satu
                    # run menabrak rate limit di detik yang sama.
                    time.sleep((2 ** attempt) + random.uniform(0, 0.5))

    raise NvidiaError("semua model gagal -> " + " | ".join(errors))
