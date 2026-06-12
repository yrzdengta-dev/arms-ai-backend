"""Unit tests: download_pdf handles non-PDF files gracefully (Slice 1).

Before: non-%PDF- content raised PdfDownloadError (PDF_INVALID_HEADER / PDF_NOT_PDF).
After:  non-%PDF- content returns DownloadResult with is_pdf=False.
"""

import pytest

from app.adapters.pdf.downloader import DownloadResult, PdfDownloadError, download_pdf


# ---------------------------------------------------------------------------
# Test 1: DownloadResult dataclass
# ---------------------------------------------------------------------------

class TestDownloadResult:
    """DownloadResult dataclass carries full download outcome."""

    def test_fields(self):
        result = DownloadResult(
            content=b"%PDF-1.4 fake pdf",
            sha256="abc123",
            is_pdf=True,
            content_type="application/pdf",
            size_bytes=18,
        )
        assert result.content == b"%PDF-1.4 fake pdf"
        assert result.sha256 == "abc123"
        assert result.is_pdf is True
        assert result.content_type == "application/pdf"
        assert result.size_bytes == 18

    def test_non_pdf_has_empty_sha256(self):
        """Non-PDF files don't need sha256 (not uploaded to MinIO)."""
        result = DownloadResult(
            content=b"\x89PNG\r\n\x1a\nfake",
            sha256="",
            is_pdf=False,
            content_type="image/png",
            size_bytes=10,
        )
        assert result.sha256 == ""
        assert result.is_pdf is False


# ---------------------------------------------------------------------------
# Test 2: download_pdf() returns DownloadResult for valid PDF
# ---------------------------------------------------------------------------

class TestDownloadPdfReturnsDownloadResult:
    @pytest.mark.asyncio
    async def test_valid_pdf_returns_is_pdf_true(self, monkeypatch):
        """A valid %PDF- response returns DownloadResult with is_pdf=True."""
        _patch_download(monkeypatch, content=b"%PDF-1.7\nfake pdf body", content_type="application/pdf")

        result = await download_pdf("https://example.com/real.pdf")
        assert isinstance(result, DownloadResult)
        assert result.is_pdf is True
        assert result.content == b"%PDF-1.7\nfake pdf body"
        assert result.content_type == "application/pdf"
        assert result.size_bytes == len(b"%PDF-1.7\nfake pdf body")
        assert result.sha256 != ""


# ---------------------------------------------------------------------------
# Test 3: download_pdf() is_pdf=False for PNG magic bytes
# ---------------------------------------------------------------------------

class TestDownloadPdfNonPdf:
    @pytest.mark.asyncio
    async def test_png_magic_returns_is_pdf_false(self, monkeypatch):
        """PNG magic bytes → is_pdf=False, no exception raised."""
        _patch_download(monkeypatch, content=b"\x89PNG\r\n\x1a\nfake png body", content_type="image/png")

        result = await download_pdf("https://example.com/screenshot.png")
        assert result.is_pdf is False
        assert result.content_type == "image/png"
        assert result.sha256 == ""

    @pytest.mark.asyncio
    async def test_html_content_returns_is_pdf_false(self, monkeypatch):
        """HTML content (e.g. login page redirect) → is_pdf=False, no exception."""
        html = b"<!DOCTYPE html>\n<html><body>Login</body></html>"
        _patch_download(monkeypatch, content=html, content_type="text/html")

        result = await download_pdf("https://example.com/login-redirect")
        assert result.is_pdf is False
        assert result.content_type == "text/html"

    @pytest.mark.asyncio
    async def test_jpg_content_returns_is_pdf_false(self, monkeypatch):
        """JPEG magic bytes → is_pdf=False."""
        _patch_download(monkeypatch, content=b"\xff\xd8\xff\xe0\x00\x10JFIF", content_type="image/jpeg")

        result = await download_pdf("https://example.com/photo.jpg")
        assert result.is_pdf is False

    @pytest.mark.asyncio
    async def test_non_pdf_does_not_raise(self, monkeypatch):
        """Non-PDF content must NOT raise PdfDownloadError."""
        _patch_download(monkeypatch, content=b"just some random bytes", content_type="application/octet-stream")

        # Must not raise
        result = await download_pdf("https://example.com/unknown.bin")
        assert result.is_pdf is False

    @pytest.mark.asyncio
    async def test_empty_pdf_header_variant(self, monkeypatch):
        """Content that looks almost like PDF but isn't → is_pdf=False."""
        # Missing the '-' after %PDF
        _patch_download(monkeypatch, content=b"%PDF 1.4\n...", content_type="application/pdf")

        result = await download_pdf("https://example.com/broken.pdf")
        assert result.is_pdf is False


# ---------------------------------------------------------------------------
# Error cases still raise
# ---------------------------------------------------------------------------

class TestDownloadPdfErrorsStillRaise:
    @pytest.mark.asyncio
    async def test_403_still_raises(self, monkeypatch):
        """HTTP errors still raise PdfDownloadError."""
        _patch_download(monkeypatch, status=403, headers={"content-type": "text/html"})

        with pytest.raises(PdfDownloadError) as exc:
            await download_pdf("https://example.com/forbidden.pdf")
        assert "FORBIDDEN" in exc.value.code.upper()

    @pytest.mark.asyncio
    async def test_404_still_raises(self, monkeypatch):
        _patch_download(monkeypatch, status=404, headers={"content-type": "text/html"})

        with pytest.raises(PdfDownloadError) as exc:
            await download_pdf("https://example.com/notfound.pdf")
        assert "NOT_FOUND" in exc.value.code.upper()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status=200, headers=None, content=b""):
        self.status_code = status
        self.headers = headers or {}
        self._content = content

    async def aiter_bytes(self):
        yield self._content


class _FakeStreamCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *a):
        pass


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def stream(self, method, url):
        return _FakeStreamCtx(self._response)


class _FakeClientCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return _FakeClient(self._response)

    async def __aexit__(self, *a):
        pass


class _FakeTransport:
    async def aclose(self):
        pass


def _patch_download(monkeypatch, *, content=b"", content_type="application/pdf", status=200, headers=None):
    """Patch all external dependencies of download_pdf for unit testing."""
    if headers is None:
        headers = {"content-type": content_type}

    response = _FakeResponse(status=status, headers=headers, content=content)

    # Mock _make_pinned_transport to skip DNS
    async def fake_make_pinned_transport(url):
        return _FakeTransport()

    monkeypatch.setattr(
        "app.adapters.pdf.downloader._make_pinned_transport",
        fake_make_pinned_transport,
    )
    # Mock AsyncClient to return our fake response
    monkeypatch.setattr(
        "app.adapters.pdf.downloader.httpx.AsyncClient",
        lambda **kw: _FakeClientCtx(response),
    )
    # Mock URL validator to pass through
    monkeypatch.setattr(
        "app.adapters.pdf.downloader.validate_pdf_url", lambda u: u,
    )
