"""Thin REST wrapper untuk Invezgo API.

Base URL  : https://api.invezgo.com
Auth      : header `Authorization: Bearer <API_KEY>`

Path endpoint di bawah diturunkan dari SDK resmi Invezgo (invezgo-go-sdk).

CONFIRMED (terlihat eksplisit di source SDK):
  /analysis/summary/stock/{code}      -> broker summary per saham (S3, S4)
  /analysis/momentum-chart/{code}     -> buy/sell done (done by offer/bid) (S1, S2)
  /analysis/order-book/{code}         -> order book / closing queue (S5)
  /analysis/inventory-chart/stock/{code}
  /analysis/top/foreign               -> foreign accumulation/distribution (S8)
  /analysis/chart/stock/{code}        -> OHLCV harian saham (S6, S7) (GetStockChart)
  /analysis/chart/index/{code}        -> OHLCV harian index/IHSG (S9) (GetIndexChart)
  /analysis/intraday-data/{code}      -> intraday real-time (mode live) (GetIntradayData)
  /usage/api

NEEDS-VERIFY (method ada di SDK, tapi path literal belum dikonfirmasi):
  daftar saham  (GetStockList)  -> stock_list()  # TODO(verify)
  daftar index  (GetIndexList)  -> index_list()  # TODO(verify)

Catatan: path di atas dikonfirmasi dari source invezgo-go-sdk (analysis.go).
Response SHAPE (nama field JSON) tiap endpoint tetap dicek pada call live
pertama lewat `scripts/verify_data.py` sebelum produksi.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

import requests


class InvezgoError(RuntimeError):
    """Error dari pemanggilan Invezgo API."""


def _parse_retry_after(value: str | None) -> float | None:
    """Header Retry-After (detik) -> float, atau None bila absen/tak valid.

    Hanya menangani bentuk delta-detik (mis. '5'); format HTTP-date diabaikan.
    """
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class _RateLimiter:
    """Throttle proaktif: maksimal `max_per_min` request dalam jendela 60 detik.

    Mencegah kena HTTP 429 saat scan universe besar (mis. LQ45). Plan Developer
    Invezgo membatasi 250-500 req/menit tergantung tier. Thread-safe (lock) agar
    aman bila client dipakai dari beberapa thread.
    """

    def __init__(self, max_per_min: int) -> None:
        self.max_per_min = max_per_min
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self.max_per_min <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                # buang timestamp yang sudah > 60 detik.
                while self._hits and now - self._hits[0] >= 60.0:
                    self._hits.popleft()
                if len(self._hits) < self.max_per_min:
                    # catat `now` yang SAMA dipakai eviksi (bukan re-sample).
                    self._hits.append(now)
                    return
                sleep_for = 60.0 - (now - self._hits[0])
            # sleep DI LUAR lock supaya thread lain tetap bisa berhitung.
            if sleep_for > 0:
                time.sleep(sleep_for)


class InvezgoClient:
    """Client minimal untuk endpoint yang dipakai Markup Radar."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.invezgo.com",
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        rate_limit_per_min: int = 250,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key:
            raise InvezgoError("INVEZGO_API_KEY belum di-set (lihat config/.env.example).")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._limiter = _RateLimiter(rate_limit_per_min)
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------ #
    # Low-level
    # ------------------------------------------------------------------ #
    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        params = {k: v for k, v in (params or {}).items() if v is not None}

        last_exc: Exception | None = None
        retry_after: float | None = None
        for attempt in range(self.max_retries):
            try:
                self._limiter.acquire()
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 429:  # rate limit -> hormati Retry-After
                    retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                    raise InvezgoError("rate limited (429)")
                resp.raise_for_status()
                payload = resp.json()
                # Invezgo umumnya membungkus hasil dalam {"data": ...}.
                if isinstance(payload, dict) and "data" in payload:
                    return payload["data"]
                return payload
            except (requests.RequestException, InvezgoError) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    # Retry-After (jika server kirim) menang atas backoff; cap 60s.
                    backoff = retry_after if retry_after is not None else 2 ** attempt
                    time.sleep(min(backoff, 60.0))  # 1s, 2s, 4s default
                    retry_after = None
        raise InvezgoError(f"GET {path} gagal setelah {self.max_retries}x: {last_exc}")

    # ------------------------------------------------------------------ #
    # Endpoints (CONFIRMED)
    # ------------------------------------------------------------------ #
    def broker_summary_stock(
        self,
        code: str,
        date_from: str,
        date_to: str,
        *,
        investor: str = "all",
        market: str | None = "RG",
    ) -> Any:
        """Broker summary per saham (S3, S4).

        `investor` WAJIB (server 422 bila kosong): 'all' | 'F' | 'D'.
        """
        return self._get(
            f"/analysis/summary/stock/{code}",
            {"from": date_from, "to": date_to, "investor": investor, "market": market},
        )

    def momentum_chart(
        self,
        code: str,
        date: str,
        *,
        range_: int = 1,
        scope: str = "value",
    ) -> Any:
        """Buy/sell done — basis done-by-offer vs done-by-bid (S1, S2).

        Param WAJIB (server 422 bila kosong): `range` (number, hari) &
        `scope` enum 'value' (rupiah) | 'volume' (lot).
        """
        return self._get(
            f"/analysis/momentum-chart/{code}",
            {"date": date, "range": range_, "scope": scope},
        )

    def order_book(self, code: str, *, market: str | None = "RG") -> Any:
        """Order book / closing queue (S5)."""
        return self._get(f"/analysis/order-book/{code}", {"market": market})

    def top_foreign(self, date: str) -> Any:
        """Top foreign accumulation/distribution (S8)."""
        return self._get("/analysis/top/foreign", {"date": date})

    def inventory_chart_stock(
        self,
        code: str,
        date_from: str,
        date_to: str,
        *,
        scope: str = "value",
        investor: str = "all",
        market: str = "RG",
        **params: Any,
    ) -> Any:
        """Inventory chart per broker (S3). `scope`/`investor`/`market` WAJIB.

        Response: {price:[OHLCV], broker:[{broker,name,data:[{date,value}]}]}
        dengan `value` = net kumulatif per broker (negatif = distribusi).
        """
        return self._get(
            f"/analysis/inventory-chart/stock/{code}",
            {
                "from": date_from,
                "to": date_to,
                "scope": scope,
                "investor": investor,
                "market": market,
                **params,
            },
        )

    def indicator_chart(
        self, indicator: str, code: str, date_from: str, date_to: str
    ) -> Any:
        return self._get(
            f"/analysis/chart/stock/{indicator}/{code}",
            {"from": date_from, "to": date_to},
        )

    def api_usage(self) -> Any:
        return self._get("/usage/api")

    # ------------------------------------------------------------------ #
    # Endpoints (NEEDS-VERIFY — konfirmasi path via /documentation)
    # ------------------------------------------------------------------ #
    def stock_chart(self, code: str, date_from: str, date_to: str) -> Any:
        """OHLCV harian saham (S6, S7).

        Path dikonfirmasi dari invezgo-go-sdk GetStockChart.
        """
        return self._get(
            f"/analysis/chart/stock/{code}",
            {"from": date_from, "to": date_to},
        )

    def index_chart(self, code: str, date_from: str, date_to: str) -> Any:
        """OHLCV harian index — IHSG/LQ45 dst (S9).

        Path dikonfirmasi dari invezgo-go-sdk GetIndexChart. Kode IHSG
        ('COMPOSITE') masih perlu dicek pada call live pertama.
        """
        return self._get(
            f"/analysis/chart/index/{code}",
            {"from": date_from, "to": date_to},
        )

    def intraday_chart(self, code: str, market: str = "RG") -> Any:
        """Intraday real-time O/H/L/C/volume (mode live).

        Path dikonfirmasi dari invezgo-go-sdk GetIntradayData.
        """
        return self._get(f"/analysis/intraday-data/{code}", {"market": market})

    def stock_list(self) -> Any:
        """Daftar seluruh saham BEI (~1200 emiten: code, name, sector, logo).

        Path diverifikasi dari bundle MCP resmi Invezgo + probe live 2026-07-16
        (tebakan lama '/stocks' SALAH).
        """
        return self._get("/analysis/list/stock")

    def index_list(self) -> Any:
        """Daftar index (COMPOSITE/IHSG, LQ45, dst).

        Path diverifikasi dari bundle MCP resmi + probe live 2026-07-16
        (tebakan lama '/indexes' SALAH).
        """
        return self._get("/analysis/list/index")

    # ------------------------------------------------------------------ #
    # Insider / kepemilikan / corporate action / berita
    # Path dikonfirmasi dari invezgo-go-sdk (analysis.go + others.go,
    # dicek 2026-07-04). Shape response diverifikasi via
    # scripts/verify_insider.py sebelum dipakai produksi.
    # ------------------------------------------------------------------ #
    def shareholder_insider(
        self,
        date_from: str,
        date_to: str,
        *,
        code: str | None = None,
        name: str | None = None,
        page: int | None = None,
        limit: int | None = None,
    ) -> Any:
        """Transaksi insider (laporan kepemilikan direksi/komisaris/PSP).

        Tanpa `code` -> market-wide (hemat kuota: 1 call utk semua saham,
        intersect dgn watchlist dilakukan lokal).
        """
        return self._get(
            "/analysis/shareholder-insider",
            {"from": date_from, "to": date_to, "code": code, "name": name,
             "page": page, "limit": limit},
        )

    def shareholder_one(
        self,
        date_from: str,
        date_to: str,
        *,
        code: str | None = None,
        name: str | None = None,
        page: int | None = None,
        limit: int | None = None,
    ) -> Any:
        """Perubahan kepemilikan >1% (keterbukaan informasi kepemilikan)."""
        return self._get(
            "/analysis/shareholder-one",
            {"from": date_from, "to": date_to, "code": code, "name": name,
             "page": page, "limit": limit},
        )

    def shareholder_above(
        self,
        date_from: str,
        date_to: str,
        *,
        code: str | None = None,
        name: str | None = None,
        broker: str | None = None,
        page: int | None = None,
        limit: int | None = None,
    ) -> Any:
        """Perubahan kepemilikan >5%."""
        return self._get(
            "/analysis/shareholder-above",
            {"from": date_from, "to": date_to, "code": code, "name": name,
             "broker": broker, "page": page, "limit": limit},
        )

    def shareholder_ksei(self, code: str, *, range_: int = 6) -> Any:
        """Breakdown kepemilikan KSEI bulanan per tipe investor (lembar).

        Response: [{code, date, price, foreign_is/cp/pf/ib/id/mf/sc/fd/ot,
        local_...}] — `id` = individual (ritel). Sum semua komponen 1 baris
        = total saham tercatat bulan itu (diverifikasi WINR 2026-07-04).
        """
        return self._get(f"/analysis/shareholder/ksei/{code}", {"range": range_})

    def shareholder_composition(self, code: str) -> Any:
        """Komposisi pemegang saham: [{name, percentage, badge}] —
        badge mis. '{PENGENDALI}', '{DIREKSI,PENGENDALI}'."""
        return self._get(f"/analysis/shareholder/{code}")

    def calendar(
        self,
        *,
        code: str | None = None,
        type_: str | None = None,
        page: int | None = None,
        limit: int | None = None,
    ) -> Any:
        """Kalender corporate action (RUPS, dividen, dst)."""
        return self._get(
            "/analysis/calendar",
            {"code": code, "type": type_, "page": page, "limit": limit},
        )

    def stock_posts(self, code: str, *, page: int = 1, limit: int = 10) -> Any:
        """Feed postingan per emiten (= feed dashboard Beranda).

        `page` & `limit` WAJIB dua-duanya (server 422 bila salah satu absen).
        Response: {totalPage, page, nextPage, data:[{id, username, content,
        created_at, ...}]} — content post 'Invezgo Report' berupa tag
        <report title=".." code=".." url="<pdf IDX>" type="shareholder|..">.
        """
        return self._get(f"/posts/space/{code}", {"page": page, "limit": limit})

    def stock_category_posts(
        self, code: str, category: str, *, page: int = 1, limit: int = 10
    ) -> Any:
        """Feed postingan per emiten difilter kategori.

        Slug VERIFIED dari bundle MCP resmi Invezgo (2026-07-16):
        'NEWS' = berita, 'REPORT' = laporan/keterbukaan informasi.
        """
        return self._get(
            f"/posts/space/category/{code}/{category}",
            {"page": page, "limit": limit},
        )

    # ------------------------------------------------------------------ #
    # Endpoint tambahan — path + param diverifikasi dari bundle MCP resmi
    # Invezgo (invezgo-mcp.mcpb v1.0.0, dist/tools/stock/handler.js +
    # dist/schema/stock.js) dan probe live 2026-07-16. Catatan probe:
    # broker stalker WAJIB from/to/investor/market (handler resmi malah
    # tidak mengirim -> 422); sector/rotation selalu balik [] -> tak dipakai.
    # ------------------------------------------------------------------ #
    def shareholder_number(self, code: str) -> Any:
        """Tren JUMLAH investor per bulan: [{code, date, value, price}].

        `value` = jumlah investor tercatat. Menyusut saat harga naik =
        barang menggumpal ke tangan kuat (bandarmologi); melonjak saat
        pucuk = distribusi ke ritel.
        """
        return self._get(f"/analysis/shareholder/number/{code}")

    def financial_statement(
        self, code: str, *, statement: str = "IS", type_: str = "Q", limit: int = 4
    ) -> Any:
        """Laporan keuangan (tabel {rows, columns}).

        `statement`: BS (neraca) | IS (laba rugi) | CF (arus kas) | EQ (ekuitas).
        `type_`: Q (kuartal berjalan) | FY (tahunan) | Q1..Q4.
        """
        return self._get(
            f"/analysis/financial-statement/{code}",
            {"statement": statement, "type": type_, "limit": limit},
        )

    def keystat(self, code: str, *, type_: str = "Q", limit: int = 4) -> Any:
        """Key statistics/rasio fundamental (tabel {rows, columns}).

        `type_`: Q | FY | Q1..Q4.
        """
        return self._get(f"/analysis/keystat/{code}", {"type": type_, "limit": limit})

    def top_change(self, date: str) -> Any:
        """Top movers 1 tanggal: {gain: [...], loss: [...]}."""
        return self._get("/users/top/change", {"date": date})

    def top_accumulation(self, date: str) -> Any:
        """Top akumulasi/distribusi 1 tanggal: {accum: [...], dist: [...]}.

        Beda dari top_foreign (khusus asing) — ini akumulasi keseluruhan.
        """
        return self._get("/users/top/accumulation", {"date": date})

    def broker_stalker(
        self,
        broker: str,
        stock: str,
        date_from: str,
        date_to: str,
        *,
        investor: str = "all",
        market: str = "RG",
    ) -> Any:
        """Lacak net flow SATU broker di SATU saham per hari.

        Response: {brokers, stock, summary{active,total,avg,peak},
        calendar:[{date, value, buy_value, sell_value}]}. `investor`/
        `market` WAJIB (server 422 bila absen).
        """
        return self._get(
            f"/analysis/stalker/broker/{broker}/{stock}",
            {"from": date_from, "to": date_to, "investor": investor, "market": market},
        )

    def order_queue(
        self, code: str, price: float, side: str, *, page: int = 0, limit: int = 50
    ) -> Any:
        """Antrian order per LEVEL HARGA (order tracking, S5 live depth).

        `side`: BUY | SELL. Response: list order individual {time, order_id,
        order_volume, open_volume, done_volume, order_value, ...} — bedakan
        order kakap vs recehan ritel di satu level harga.
        """
        return self._get(
            f"/analysis/queue/{code}",
            {"price": price, "side": side, "page": page, "limit": limit},
        )

    def shareholder_high(self) -> Any:
        """Daftar market-wide emiten kepemilikan TERKONSENTRASI tinggi.

        Response: [{code, date, percentage}] — percentage ~90%+ = float
        publik tipis/terkunci (kandidat gampang di-markup). 1 call untuk
        seluruh market (hemat kuota, cocok discovery).
        """
        return self._get("/analysis/shareholder/high")
