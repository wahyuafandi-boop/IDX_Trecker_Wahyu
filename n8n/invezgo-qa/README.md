# Invezgo Q&A — Telegram bot (n8n + MCP connector)

Tanya-jawab analisa saham IDX lewat Telegram. Claude (via Anthropic Messages API
**MCP connector**) memanggil tool Invezgo **server-side** ke MCP publik
`https://mcp-invezgo.wahyuafandi.my.id/mcp`, lalu balas ke Telegram.

**Sudah diverifikasi live (2026-07-13):** connector Anthropic berhasil auth (Bearer)
+ panggil tool `check_quota` ke MCP publik → balik data Invezgo asli → Claude jawab.
Jadi jalur otak→MCP→Invezgo terbukti jalan; sisa cuma wiring di n8n (langkahmu).

Arsitektur: `Telegram Trigger → (Ack "lagi narik data" ∥ Build Request) → Claude (MCP) HTTP
→ Extract Answer → Sanitize Markdown → Telegram Reply (→ error: Reply Plain fallback)`.
Anti-diam: node Claude `onError=continueRegularOutput` (timeout/API error → bot balas
"⚠️ gagal, coba lagi" alih-alih hening); Telegram Reply `onError=continueErrorOutput` →
Reply Plain tanpa parse_mode (Markdown ditolak Telegram → jawaban tetap terkirim polos);
jawaban dipotong di ~4000 char (batas Telegram). Timeout Claude 600 dtk (pertanyaan
berat multi-tool bisa 2-5 menit). MCP server menyediakan **19 tool** (11 awal + 8 porting
dari katalog MCP resmi Invezgo, verified 2026-07-16: shareholder_number, financials,
keystats, top_movers, top_accumulation, broker_stalker, order_queue, high_concentration).

## File
- `workflow.json` — workflow n8n siap import.
- `system-prompt.md` — "otak" (S1–S11, state, regime, tesis bandarmologi, rambu). Sudah
  tertanam di node **Build Request**; edit di sana kalau mau ubah (atau regen dari file ini).

## Prasyarat (di n8n kamu)
1. **Credential Anthropic** — httpHeaderAuth bernama "Anthropic API Key" yang mengeset
   header `x-api-key: sk-ant-...` (sama seperti dipakai workflow trading-agent).
2. **Credential Telegram** — `telegramApi` (bot). Boleh **bot markup-radar yang sudah ada**
   (chat 6788518859). ⚠️ Satu bot hanya boleh punya SATU konsumen update: kalau bot itu
   juga dipakai trigger di workflow lain (mis. trading-agent), **pakai bot terpisah** biar
   nggak rebutan getUpdates. Markup-radar cuma *kirim* alert (bukan trigger) → aman dipakai.

## Import & wiring
1. n8n → **Workflows → Import from File** → pilih `workflow.json`.
2. Node **Build Request** (Code) → di atas, ganti:
   - `PASTE_MCP_BEARER_TOKEN_HERE` → token MCP. Ambil:
     ```
     ssh -i ~/.ssh/idx_agent_vps -p 2222 root@144.91.71.3 \
       "grep MCP_AUTH_TOKEN /root/IDX_Trecker_Wahyu/docker/invezgo-mcp/mcp.env"
     ```
   - (opsional) `MODEL` → `claude-opus-4-8` untuk analisa berat (default `claude-sonnet-5`).
3. Node **Telegram Trigger** & **Telegram Reply** → pilih credential Telegram-mu.
4. Node **Claude (MCP)** → pilih credential "Anthropic API Key".
5. **Activate** workflow (toggle kanan atas).
6. Test: chat ke bot di Telegram, mis. *"BBCA lagi akumulasi atau distribusi? pakai data 20 hari"*.

## Biaya & kuota (penting)
- **Model:** Sonnet 5 (default) murah & kuat untuk Q&A tool-heavy; Opus 4.8 paling pintar tapi
  ~5x lebih mahal — pakai untuk pertanyaan berat saja. Adaptive thinking + effort `medium`.
- **Kuota Invezgo:** tiap tool call bot masuk **budget MCP** (500/hari, 5000/bln) + jatah
  30k/bln yang dipakai bareng cron EOD/live. Bot yang cerewet perlu naikin cap
  (`MCP_MAX_CALLS_PER_DAY` di mcp.env / compose) — tetap jaga sisa buat cron.
- Sweep market-wide (ownership/insider tanpa kode) di-gate `needs_confirm`; Claude fokus per-kode
  kecuali kamu minta scan seluruh market.

## Catatan
- Pertanyaan yang butuh >10 langkah tool bisa balik `stop_reason: pause_turn` (jarang untuk
  analisa 1–2 saham). Node Extract Answer memberi tahu kalau kejadian; persempit pertanyaan.
- Jawaban Telegram maks ~4096 char — system prompt sudah minta ringkas (5–12 baris).
- Beta header wajib: `anthropic-beta: mcp-client-2025-11-20` (sudah diset di node Claude).
