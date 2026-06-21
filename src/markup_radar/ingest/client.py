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
        """Daftar seluruh saham BEI. TODO(verify): path literal."""
        return self._get("/stocks")  # TODO(verify)

    def index_list(self) -> Any:
        """Daftar index (IHSG, LQ45, dst). TODO(verify): path literal."""
        return self._get("/indexes")  # TODO(verify)
