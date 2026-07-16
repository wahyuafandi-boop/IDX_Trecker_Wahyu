"""MCP server (Streamable HTTP) untuk data Invezgo — dukung sinyal Markup Radar.

Menjembatani Claude (chat) ke Invezgo API lewat MCP remote. Setiap tool = 1
pemanggilan `InvezgoClient` yang sudah ada (rate-limiter 250/menit + retry +
unwrap `{"data": ...}` reuse dari `markup_radar.ingest`).

Transport : Streamable HTTP (stateless) di path `/mcp`.
Auth      : header `Authorization: Bearer <MCP_AUTH_TOKEN>` (fail-closed —
            server nolak start bila token kosong). `/health` bebas auth utk
            probe reverse-proxy.
Guard kuota (paket Advance 30k/bln, dipakai bareng cron harian):
  - cap per-sesi proses (`MCP_MAX_CALLS_PER_SESSION`, default 300) — cegah loop
    tool nggerus jatah cron;
  - tool sweep MARKET-WIDE (ownership crossing / insider tanpa `code`) minta
    `confirm=true` sebelum eksekusi;
  - `check_quota()` lapor sisa kuota live + hitung call sesi ini.

Jalankan (lokal / VPS di belakang proxy):
    MCP_AUTH_TOKEN=xxxx python -m markup_radar.mcp_server.server
Env opsional: MCP_HOST (default 127.0.0.1), MCP_PORT (default 8848),
INVEZGO_API_KEY (dari .env / environment).
"""

from __future__ import annotations

import hmac
import os
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from markup_radar.config import load_settings
from markup_radar.ingest import InvezgoClient, InvezgoError
from markup_radar.mcp_server.budget import BudgetExceeded, QuotaBudget

# --------------------------------------------------------------------------- #
# Client + config (satu instance per proses, thread-safe via _RateLimiter lock)
# --------------------------------------------------------------------------- #
_cfg = load_settings()

# Client di-init malas (lazy): impor modul (mis. utk test/tooling/CI) tak butuh
# INVEZGO_API_KEY — key baru wajib saat tool pertama benar-benar dipanggil, dan
# InvezgoError-nya tertangkap rapi oleh `_run`.
_client_cache: dict[str, InvezgoClient] = {}


def _api() -> InvezgoClient:
    client = _client_cache.get("c")
    if client is None:
        client = InvezgoClient(
            _cfg.invezgo_api_key,
            _cfg.invezgo_base_url,
            rate_limit_per_min=_cfg.rate_limit_per_min,
        )
        _client_cache["c"] = client
    return client

# Budget guard PERSISTEN (harian + bulanan): pagar utama supaya konsumsi kuota
# lewat MCP tak menggerus jatah cron EOD/live — aman walau token bocor. Persisten
# ke disk → restart service tak me-reset (lihat budget.py). Cap via env.
_budget = QuotaBudget(
    os.getenv("MCP_QUOTA_FILE", "data/mcp_quota.json"),
    daily_cap=int(os.getenv("MCP_MAX_CALLS_PER_DAY", "500")),
    monthly_cap=int(os.getenv("MCP_MONTHLY_BUDGET", "5000")),
)


def _run(cost: int, thunk: Callable[[], Any]) -> Any:
    """Bingkai eksekusi tiap tool: budget-guard → panggil → error rapi.

    Return dict `{"error": ...}` (bukan raise) supaya di sisi chat tetap terbaca
    sebagai hasil tool, bukan crash koneksi.
    """
    try:
        _budget.spend(cost)  # cek cap SEBELUM call; call gagal pun tetap kena kuota
    except BudgetExceeded as exc:
        return {
            "error": "quota_guard",
            "scope": exc.scope,
            "message": (
                f"Budget MCP {exc.scope} habis ({exc.used}/{exc.cap} call). Pagar ini "
                "menjaga jatah cron harian. Naikkan env budget atau tunggu reset."
            ),
            **_budget.snapshot(),
        }
    try:
        return thunk()
    except InvezgoError as exc:
        return {"error": "invezgo", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 — batas luar: jangan matikan koneksi MCP
        return {"error": "unexpected", "message": f"{type(exc).__name__}: {exc}"}


mcp = FastMCP(
    "markup-radar-invezgo",
    instructions=(
        "Data pasar IDX dari Invezgo untuk mendukung sinyal Markup Radar (Wyckoff + "
        "bandarmologi). Hemat kuota: 1 tool = 1 call API. Untuk analisis 1 emiten, "
        "mulai dari get_stock_ohlcv + get_broker_summary + get_done_momentum. Tool "
        "MARKET-WIDE (get_ownership_crossings / get_insider_transactions tanpa code) "
        "butuh confirm=true. Cek sisa kuota via check_quota bila ragu."
    ),
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8848")),
    streamable_http_path="/mcp",
    stateless_http=True,
)


# --------------------------------------------------------------------------- #
# TOOLS — core tape (sinyal S1-S9)
# --------------------------------------------------------------------------- #
@mcp.tool()
def get_stock_ohlcv(code: str, date_from: str, date_to: str) -> Any:
    """OHLCV harian 1 saham (basis S6 RVOL & S7 close-in-range).

    `date_from`/`date_to` format 'YYYY-MM-DD'. Endpoint chart Invezgo membatasi
    rentang ~6 bulan/panggilan; horizon histori ~2 tahun. Return: list bar harian.
    """
    return _run(1, lambda: _api().stock_chart(code, date_from, date_to))


@mcp.tool()
def get_broker_summary(
    code: str, date_from: str, date_to: str, investor: str = "all"
) -> Any:
    """Broker summary net buy/sell per saham (basis S3 akumulasi & S4 konsentrasi).

    `investor`: 'all' | 'F' (asing) | 'D' (domestik). Pakai untuk lihat broker
    mana yang net-akumulasi (bandar) vs distribusi.
    """
    return _run(
        1, lambda: _api().broker_summary_stock(code, date_from, date_to, investor=investor)
    )


@mcp.tool()
def get_done_momentum(
    code: str, date: str, range_days: int = 1, scope: str = "value"
) -> Any:
    """Done by offer vs done by bid — momentum tape (basis S1 done-ratio & S2 absorpsi).

    `scope`: 'value' (rupiah) | 'volume' (lot). `range_days`: jumlah hari ke belakang.
    Done-by-offer dominan = buyer agresif angkat harga (markup); dominan bid = seller.
    """
    return _run(
        1, lambda: _api().momentum_chart(code, date, range_=range_days, scope=scope)
    )


@mcp.tool()
def get_order_book(code: str) -> Any:
    """Order book / antrian bid-offer real-time (basis S5 queue imbalance).

    LIVE only (order book tak historis). Bid >> offer di sekitar close = demand
    antri = konfirmasi tier MARKUP_CONFIRMED.
    """
    return _run(1, lambda: _api().order_book(code))


@mcp.tool()
def get_top_foreign(date: str) -> Any:
    """Ranking akumulasi/distribusi asing se-market pada 1 tanggal (basis S8).

    `date` 'YYYY-MM-DD'. Guna ganda: (a) cek apakah kode watchlist ada di sisi
    akumulasi asing; (b) FUNNEL discovery — nama teratas = kandidat markup baru.
    Satu call (bukan per-saham).
    """
    return _run(1, lambda: _api().top_foreign(date))


# --------------------------------------------------------------------------- #
# TOOLS — endpoint nganggur bernilai tinggi (dulu di-wrap tapi tak dipanggil)
# --------------------------------------------------------------------------- #
@mcp.tool()
def get_ownership_crossings(
    date_from: str,
    date_to: str,
    threshold: str = "5",
    code: str | None = None,
    limit: int = 50,
    confirm: bool = False,
) -> Any:
    """Laporan crossing kepemilikan >1% / >5% (keterbukaan). Sinyal smart-money masuk.

    `threshold`: '5' (>5%, `shareholder-above`) atau '1' (>1%, `shareholder-one`).
    Pihak baru tembus 5% = bangun posisi besar → sering mendahului markup. Tanpa
    `code` = MARKET-WIDE (mahal) → butuh `confirm=true`. `limit` server maks ~50.
    """
    if code is None and not confirm:
        return {
            "needs_confirm": True,
            "message": (
                "Sweep MARKET-WIDE (1 call besar, semua emiten). Panggil ulang "
                "dgn confirm=true, atau isi `code` utk 1 emiten saja. Cek sisa "
                "kuota via check_quota."
            ),
        }
    if str(threshold) == "1":
        thunk = lambda: _api().shareholder_one(  # noqa: E731
            date_from, date_to, code=code, limit=limit
        )
    else:
        thunk = lambda: _api().shareholder_above(  # noqa: E731
            date_from, date_to, code=code, limit=limit
        )
    return _run(1, thunk)


@mcp.tool()
def get_stock_news(code: str, category: str | None = None, limit: int = 10) -> Any:
    """Feed keterbukaan/berita per emiten (KATALIS di balik pergerakan harga).

    Pakai untuk jawab 'kenapa volume spike': kontrak/laba = konfirmasi; rights
    issue dilutif = red flag/trap. `category`: 'NEWS' (berita) | 'REPORT'
    (laporan/keterbukaan informasi) — slug verified; kosong = feed penuh.
    Konten 'Invezgo Report' berisi tag <report ... url=PDF IDX>.
    """
    if category:
        thunk = lambda: _api().stock_category_posts(  # noqa: E731
            code, category, page=1, limit=limit
        )
    else:
        thunk = lambda: _api().stock_posts(code, page=1, limit=limit)  # noqa: E731
    return _run(1, thunk)


@mcp.tool()
def get_indicator(indicator: str, code: str, date_from: str, date_to: str) -> Any:
    """Indikator teknikal server-side Invezgo (konfirmasi/veto sekunder).

    `indicator` = slug (mis. 'rsi'/'macd'/'ma' — slug pasti perlu diprobe; endpoint
    ini NEEDS-VERIFY, belum pernah dipakai produksi). RSI belum overbought di
    MARKUP_START = masih ada ruang; divergence bearish = hati-hati.
    """
    return _run(
        1, lambda: _api().indicator_chart(indicator, code, date_from, date_to)
    )


# --------------------------------------------------------------------------- #
# TOOLS — porting dari katalog MCP resmi Invezgo (path verified 2026-07-16)
# --------------------------------------------------------------------------- #
@mcp.tool()
def get_shareholder_number(code: str) -> Any:
    """Tren JUMLAH investor per bulan (bandarmologi: konsentrasi kepemilikan).

    Response bulanan [{date, value, price}] — `value` = jumlah investor.
    Jumlah investor MENYUSUT saat harga naik = barang menggumpal ke tangan
    kuat (bullish); MELONJAK di pucuk = distribusi ke ritel (red flag).
    """
    return _run(1, lambda: _api().shareholder_number(code))


@mcp.tool()
def get_financials(
    code: str, statement: str = "IS", period: str = "Q", limit: int = 4
) -> Any:
    """Laporan keuangan emiten (konteks fundamental di balik pergerakan).

    `statement`: 'BS' neraca | 'IS' laba-rugi | 'CF' arus kas | 'EQ' ekuitas.
    `period`: 'Q' kuartal | 'FY' tahunan | 'Q1'..'Q4'. Return tabel
    {rows, columns}. Pakai saat perlu cek laba/utang/kas di balik sinyal.
    """
    return _run(
        1,
        lambda: _api().financial_statement(
            code, statement=statement, type_=period, limit=limit
        ),
    )


@mcp.tool()
def get_keystats(code: str, period: str = "Q", limit: int = 4) -> Any:
    """Rasio & key statistics fundamental (PER, ROE, dst) per periode.

    `period`: 'Q' | 'FY' | 'Q1'..'Q4'. Lebih ringkas dari get_financials —
    mulai dari sini untuk cek valuasi/kualitas fundamental cepat.
    """
    return _run(1, lambda: _api().keystat(code, type_=period, limit=limit))


@mcp.tool()
def get_top_movers(date: str) -> Any:
    """Top gainers & losers 1 tanggal: {gain: [...], loss: [...]}.

    Discovery harian — kandidat markup sering muncul di sini sebelum ramai.
    `date` 'YYYY-MM-DD'. 1 call untuk seluruh market.
    """
    return _run(1, lambda: _api().top_change(date))


@mcp.tool()
def get_top_accumulation(date: str) -> Any:
    """Top akumulasi & distribusi 1 tanggal: {accum: [...], dist: [...]}.

    Beda dari get_top_foreign (khusus asing) — ini akumulasi keseluruhan.
    Funnel discovery: nama di `accum` beberapa hari beruntun = kandidat markup.
    """
    return _run(1, lambda: _api().top_accumulation(date))


@mcp.tool()
def get_broker_stalker(
    broker: str, code: str, date_from: str, date_to: str
) -> Any:
    """Lacak net flow SATU broker di SATU saham per hari (stalking bandar).

    `broker` = kode 2 huruf (mis. CC Mandiri, AK UBS, YP Mirae). Response:
    summary (net total, hari aktif, peak) + calendar harian {date, value,
    buy_value, sell_value}. Pakai setelah get_broker_summary menunjukkan
    broker akumulator — untuk lihat POLA hariannya (konsisten vs sekali gebrak).
    """
    return _run(
        1, lambda: _api().broker_stalker(broker, code, date_from, date_to)
    )


@mcp.tool()
def get_order_queue(
    code: str, price: float, side: str = "BUY", limit: int = 20
) -> Any:
    """Antrian order per LEVEL HARGA — bedah siapa yang antri (S5 live depth).

    `side`: 'BUY' | 'SELL'. Return order individual {time, order_volume,
    done_volume, order_value, ...} di harga tsb. Order gede seragam = big
    money pasang kuda-kuda; recehan acak = ritel. LIVE (jam bursa).
    """
    return _run(1, lambda: _api().order_queue(code, price, side, limit=limit))


@mcp.tool()
def get_high_concentration() -> Any:
    """Daftar market-wide emiten kepemilikan terkonsentrasi tinggi (~90%+).

    Response [{code, date, percentage}] — percentage tinggi = float publik
    tipis/terkunci = supply gampang dikendalikan (kandidat markup, tapi juga
    rawan digoreng). 1 call untuk seluruh market — cocok discovery.
    """
    return _run(1, lambda: _api().shareholder_high())


@mcp.tool()
def search_stocks(query: str) -> Any:
    """Cari kode emiten dari nama perusahaan/kata kunci (mis. 'adaro' -> AADI/ADRO).

    Panggil ini DULU saat user menyebut nama perusahaan tanpa kode saham.
    Response {hits: [{code, name}], totalHits}.
    """
    return _run(1, lambda: _api().search_stock(query))


@mcp.tool()
def get_multi_timeframe_chart(
    code: str, date_from: str, date_to: str, timeframe: str = "W"
) -> Any:
    """OHLCV weekly/monthly/intraday — struktur besar di atas chart harian.

    `timeframe`: 'W' mingguan | 'M' bulanan | 'D' harian | '1','5','15','30',
    '60' menit. Pakai 'W' untuk cek tren swing besar sebelum baca sinyal harian.
    """
    return _run(
        1,
        lambda: _api().multi_time_chart(code, date_from, date_to, timeframe=timeframe),
    )


@mcp.tool()
def get_price_seasonality(code: str, years: int = 5) -> Any:
    """Seasonality bulanan historis: bulan apa saham ini biasanya kuat/lemah.

    Response per bulan [{month, start_price, end_price, percentage_change}]
    selama `years` tahun ke belakang. Konteks timing, bukan sinyal utama.
    """
    return _run(1, lambda: _api().price_seasonality(code, range_=years))


@mcp.tool()
def get_broker_flow_sankey(
    code: str, date: str, flow_type: str = "value"
) -> Any:
    """Aliran transaksi ANTAR broker pada 1 tanggal (siapa serap barang siapa).

    Response graph {nodes, links} — baca link terbesar: broker seller ->
    broker buyer. Crossing besar antar broker tertentu = pindah barang
    bandar. `flow_type`: 'value' (rupiah) | 'volume' (lot).
    """
    return _run(1, lambda: _api().sankey_chart(code, date, type_=flow_type))


@mcp.tool()
def get_price_volume_profile(code: str, date: str) -> Any:
    """Distribusi volume per LEVEL HARGA 1 hari (volume profile).

    Response [{price, buy_volume, sell_volume, buy_freq, sell_freq}] —
    level dengan volume menumpuk = support/resistance objektif; bandingkan
    buy vs sell volume di tiap level untuk lihat siapa menang di mana.
    """
    return _run(1, lambda: _api().price_table(code, date))


# --------------------------------------------------------------------------- #
# TOOLS — personal (READ-ONLY akun Invezgo pemilik key). OPT-IN via env
# MCP_ENABLE_PERSONAL=1 — default OFF: endpoint MCP ini publik (dgn token);
# tanpa flag, data porto/journal tak pernah ter-expose walau token bocor.
# --------------------------------------------------------------------------- #
if os.getenv("MCP_ENABLE_PERSONAL", "0") == "1":

    @mcp.tool()
    def get_my_portfolio(view: str = "positions") -> Any:
        """Portfolio pribadi di akun Invezgo (read-only).

        `view`: 'positions' (daftar posisi) | 'summary' (total nilai,
        unrealized P/L, alokasi sektor). Gabungkan dengan tool analisa utk
        review posisi ("posisi mana yang sinyalnya memburuk?").
        """
        if view == "summary":
            return _run(1, lambda: _api().portfolio_summary())
        return _run(1, lambda: _api().portfolio())

    @mcp.tool()
    def get_my_journal(date_from: str, date_to: str) -> Any:
        """Jurnal trading pribadi pada rentang tanggal (read-only)."""
        return _run(1, lambda: _api().journals(date_from, date_to))

    @mcp.tool()
    def get_my_trade_summary(date_from: str, date_to: str) -> Any:
        """Rapor trading pribadi: win_rate, total profit/loss, top_code, dst."""
        return _run(1, lambda: _api().trade_summary(date_from, date_to))

    @mcp.tool()
    def get_my_watchlist() -> Any:
        """Watchlist tersimpan di akun Invezgo (read-only)."""
        return _run(1, lambda: _api().my_watchlist())


# --------------------------------------------------------------------------- #
# TOOLS — konteks kepemilikan / insider (enrichment pendukung)
# --------------------------------------------------------------------------- #
@mcp.tool()
def get_ksei_ownership(code: str, months: int = 6) -> Any:
    """Tren kepemilikan KSEI bulanan per tipe investor (float control).

    Ritel (`*_id`) menyusut antar bulan = barang pindah ke tangan kuat (bullish
    bandarmologi). Sum semua komponen 1 baris = total saham tercatat bulan itu.
    """
    return _run(1, lambda: _api().shareholder_ksei(code, range_=months))


@mcp.tool()
def get_insider_transactions(
    date_from: str,
    date_to: str,
    code: str | None = None,
    limit: int = 50,
    confirm: bool = False,
) -> Any:
    """Transaksi insider (direksi/komisaris/PSP). Net beli = konfirmasi; jual = red flag.

    Tanpa `code` = MARKET-WIDE (mahal) → butuh `confirm=true`. `limit` server maks
    ~50 (100 balik HTTP 500).
    """
    if code is None and not confirm:
        return {
            "needs_confirm": True,
            "message": (
                "Sweep MARKET-WIDE (1 call besar). Panggil ulang dgn confirm=true, "
                "atau isi `code` utk 1 emiten. Cek sisa kuota via check_quota."
            ),
        }
    return _run(
        1,
        lambda: _api().shareholder_insider(date_from, date_to, code=code, limit=limit),
    )


# --------------------------------------------------------------------------- #
# TOOL — utility kuota
# --------------------------------------------------------------------------- #
@mcp.tool()
def check_quota() -> Any:
    """Sisa kuota Invezgo (paket Advance 30k/bln) + call MCP terpakai sesi ini.

    Panggil ini SEBELUM sweep besar atau bila ragu. Catatan: tool ini sendiri
    memakai 1 call (endpoint /usage/api).
    """
    try:
        usage = _api().api_usage()
        _budget.record(1)  # call meta ini pun dihitung, tapi tak pernah diblok
    except InvezgoError as exc:
        usage = {"error": str(exc)}
    return {"invezgo_usage": usage, "mcp_budget": _budget.snapshot()}


# --------------------------------------------------------------------------- #
# ASGI: bearer auth (pure-ASGI, tak buffer SSE) + /health + lifespan pass-through
# --------------------------------------------------------------------------- #
class _BearerAuth:
    """Middleware ASGI murni: tolak request tanpa Bearer token yang benar.

    Bukan BaseHTTPMiddleware (yang mem-buffer body → bisa merusak stream SSE
    Streamable HTTP). Lifespan & websocket diteruskan apa adanya. `/health` bebas.
    """

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path", "") == "/health":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        if not hmac.compare_digest(auth, f"Bearer {self.token}"):
            from starlette.responses import JSONResponse

            await JSONResponse({"error": "unauthorized"}, status_code=401)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)


def build_app() -> _BearerAuth:
    """Bangun ASGI app siap-uvicorn: FastMCP streamable-http + /health + auth."""
    token = os.getenv("MCP_AUTH_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "MCP_AUTH_TOKEN wajib di-set — server remote tanpa auth = bahaya."
        )

    app = mcp.streamable_http_app()

    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def health(_request: Any) -> Any:
        # Rute tanpa auth → sengaja minimal, tak bocorkan angka usage/kuota.
        return JSONResponse({"ok": True, "service": "markup-radar-invezgo-mcp"})

    app.router.routes.append(Route("/health", health, methods=["GET"]))
    return _BearerAuth(app, token)


def main() -> None:
    import uvicorn

    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8848"))
    uvicorn.run(build_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
