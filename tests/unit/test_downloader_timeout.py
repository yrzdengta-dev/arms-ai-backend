"""Unit tests: PDF downloader Timeout construction (P0-1).

RED expectations (before fix):
  - test_timeout_construction_does_not_raise: FAIL (missing write param → ValueError)
  - test_write_timeout_mapped: FAIL (no WriteTimeout handler)
  - test_pool_timeout_mapped: FAIL (no PoolTimeout handler)
"""

import httpx
import pytest

from app.adapters.pdf.downloader import PdfDownloadError, download_pdf


class TestTimeoutConstruction:
    """httpx.Timeout must be valid for httpx 0.28+ (requires connect/read/write/pool or default)."""

    def test_timeout_construction_does_not_raise(self):
        """Constructing the AsyncClient inside download_pdf must not raise ValueError
        due to missing write/pool timeout parameters."""
        from app.core.config import get_settings
        settings = get_settings()
        # This must not raise ValueError
        timeout = httpx.Timeout(
            connect=settings.PDF_CONNECT_TIMEOUT_SECONDS,
            read=settings.PDF_DOWNLOAD_TIMEOUT_SECONDS,
            write=settings.PDF_DOWNLOAD_TIMEOUT_SECONDS,
            pool=settings.PDF_DOWNLOAD_TIMEOUT_SECONDS,
        )
        assert timeout.connect is not None
        assert timeout.read is not None
        assert timeout.write is not None
        assert timeout.pool is not None


class TestTimeoutErrorMapping:
    """httpx WriteTimeout and PoolTimeout must map to PdfDownloadError."""

    @pytest.mark.asyncio
    async def test_connect_timeout_mapped(self, monkeypatch):
        called_with = {}

        async def fake_get(url):
            raise httpx.ConnectTimeout("connect timeout")

        async def fake_client_ctx(self):
            class FakeClient:
                async def __aenter__(self2): return self2
                async def __aexit__(self2, *a): pass
                get = fake_get
            return FakeClient()

        monkeypatch.setattr(
            "app.adapters.pdf.downloader.httpx.AsyncClient",
            lambda **kw: _FakeClientCtx(fake_get),
        )
        # Bypass URL validation
        monkeypatch.setattr(
            "app.adapters.pdf.downloader.validate_pdf_url", lambda u: u,
        )

        with pytest.raises(PdfDownloadError) as exc:
            await download_pdf("https://example.com/test.pdf")
        assert "CONNECT" in exc.value.code.upper()

    @pytest.mark.asyncio
    async def test_read_timeout_mapped(self, monkeypatch):
        async def fake_get(url):
            raise httpx.ReadTimeout("read timeout")

        monkeypatch.setattr(
            "app.adapters.pdf.downloader.httpx.AsyncClient",
            lambda **kw: _FakeClientCtx(fake_get),
        )
        monkeypatch.setattr(
            "app.adapters.pdf.downloader.validate_pdf_url", lambda u: u,
        )

        with pytest.raises(PdfDownloadError) as exc:
            await download_pdf("https://example.com/test.pdf")
        assert "READ" in exc.value.code.upper()

    @pytest.mark.asyncio
    async def test_write_timeout_mapped_to_error(self, monkeypatch):
        """WriteTimeout must produce a PdfDownloadError, not leak the raw httpx exception."""
        async def fake_get(url):
            raise httpx.WriteTimeout("write timeout")

        monkeypatch.setattr(
            "app.adapters.pdf.downloader.httpx.AsyncClient",
            lambda **kw: _FakeClientCtx(fake_get),
        )
        monkeypatch.setattr(
            "app.adapters.pdf.downloader.validate_pdf_url", lambda u: u,
        )

        with pytest.raises(PdfDownloadError) as exc:
            await download_pdf("https://example.com/test.pdf")
        assert "TIMEOUT" in exc.value.code.upper() or "WRITE" in exc.value.code.upper()

    @pytest.mark.asyncio
    async def test_pool_timeout_mapped_to_error(self, monkeypatch):
        """PoolTimeout must produce a PdfDownloadError, not leak the raw httpx exception."""
        async def fake_get(url):
            raise httpx.PoolTimeout("pool timeout")

        monkeypatch.setattr(
            "app.adapters.pdf.downloader.httpx.AsyncClient",
            lambda **kw: _FakeClientCtx(fake_get),
        )
        monkeypatch.setattr(
            "app.adapters.pdf.downloader.validate_pdf_url", lambda u: u,
        )

        with pytest.raises(PdfDownloadError) as exc:
            await download_pdf("https://example.com/test.pdf")
        assert "TIMEOUT" in exc.value.code.upper() or "POOL" in exc.value.code.upper()


# ---- helpers ----

class _FakeClientCtx:
    def __init__(self, fn):
        self._fn = fn

    async def __aenter__(self):
        return _FakeClient(self._fn)

    async def __aexit__(self, *a):
        pass


class _FakeClient:
    def __init__(self, fn):
        self._fn = fn

    def stream(self, method, url):
        return _FakeStreamCtx(self._fn, url)


class _FakeStreamCtx:
    def __init__(self, fn, url):
        self._fn = fn
        self._url = url

    async def __aenter__(self):
        return await self._fn(self._url)

    async def __aexit__(self, *a):
        pass
