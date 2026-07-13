"""MCP server (Streamable HTTP) yang mem-bungkus InvezgoClient.

Expose ~10 tool fokus-sinyal ke Claude via MCP remote (deploy VPS). Reuse
`markup_radar.ingest.InvezgoClient` — rate-limiter/retry/unwrap `.data` matang,
tak ditulis ulang. Lihat `server.py`.
"""
