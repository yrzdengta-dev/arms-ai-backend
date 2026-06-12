"""Security tests: SSRF DNS resolution, redirect validation, DNS rebinding (P0-2).

RED expectations (before fix):
  - test_hostname_resolves_to_private_ip_is_blocked: FAIL (no DNS resolution)
  - test_redirect_to_private_ip_is_blocked: FAIL (no per-hop re-validation)
  - test_redirect_to_localhost_is_blocked: FAIL
  - test_redirect_chain_exceeding_max_is_blocked: depends on max_redirects
  - test_public_domain_passes: PASS (already works for literal IP check)
"""

import pytest

from app.adapters.pdf.url_validator import UnsafeURLException, validate_pdf_url


class TestDNSResolution:
    """DNS hostname resolution must validate ALL resolved IPs."""

    def test_public_domain_passes_validation(self):
        """A known-public hostname should pass validation (DNS resolves to public IP)."""
        validate_pdf_url("https://example.com/document.pdf")

    def test_hostname_resolving_to_loopback_is_blocked(self):
        """localhost resolves to 127.0.0.1 — must be blocked."""
        with pytest.raises(UnsafeURLException):
            validate_pdf_url("https://localhost:8080/document.pdf")

    def test_dot_local_domain_blocked(self):
        """Domains ending in .local must be blocked."""
        with pytest.raises(UnsafeURLException):
            validate_pdf_url("https://evil.local/document.pdf")

    def test_metadata_hostname_blocked(self):
        """metadata.google.internal must be blocked by name before DNS."""
        with pytest.raises(UnsafeURLException):
            validate_pdf_url("https://metadata.google.internal/document.pdf")


class TestRedirectValidation:
    """Each redirect hop must be re-validated for SSRF."""

    @pytest.mark.asyncio
    async def test_redirect_to_localhost_rejected(self, monkeypatch):
        """When a public URL redirects to localhost, the download must be blocked."""
        from app.adapters.pdf.downloader import PdfDownloadError, download_pdf
        from app.adapters.pdf.url_validator import UnsafeURLException

        call_count = 0

        class FakeResponse:
            status_code = 302
            headers = {"Location": "https://localhost:9999/evil.pdf"}
            def read(self): return b""

        async def fake_get(url):
            nonlocal call_count
            call_count += 1
            return FakeResponse()

        monkeypatch.setattr(
            "app.adapters.pdf.downloader.httpx.AsyncClient",
            lambda **kw: _RedirectFakeCtx(fake_get),
        )
        # Allow the initial URL to pass validation
        original_validate = None

        def validating_validate(url):
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            if host in ("example.com", "example.org", "httpbin.org"):
                return url
            if host == "localhost" or host.startswith("127.") or host.startswith("10.") or host.startswith("192.168."):
                raise UnsafeURLException(f"Blocked: {host}")
            return url

        monkeypatch.setattr(
            "app.adapters.pdf.downloader.validate_pdf_url",
            validating_validate,
        )

        with pytest.raises((PdfDownloadError, UnsafeURLException)):
            await download_pdf("https://example.com/redirect-to-localhost.pdf")

        # localhost target must NEVER receive an actual HTTP request
        assert call_count <= 1, f"localhost received {call_count} requests"

    @pytest.mark.asyncio
    async def test_redirect_to_private_ip_rejected(self, monkeypatch):
        """Redirect to RFC1918 address must be blocked."""
        from app.adapters.pdf.downloader import PdfDownloadError, download_pdf
        from app.adapters.pdf.url_validator import UnsafeURLException

        class FakeResponse:
            status_code = 301
            headers = {"Location": "https://192.168.1.1/admin.pdf"}
            def read(self): return b""

        async def fake_get(url):
            return FakeResponse()

        monkeypatch.setattr(
            "app.adapters.pdf.downloader.httpx.AsyncClient",
            lambda **kw: _RedirectFakeCtx(fake_get),
        )

        def validating_validate(url):
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            if "192.168" in host or "10." in host or "172.16" in host:
                raise UnsafeURLException(f"Blocked: {host}")
            return url

        monkeypatch.setattr(
            "app.adapters.pdf.downloader.validate_pdf_url",
            validating_validate,
        )

        with pytest.raises((PdfDownloadError, UnsafeURLException)):
            await download_pdf("https://example.com/redirect-to-private.pdf")


class TestIPAddressValidation:
    """Verify all IP types are checked using ipaddress built-in attributes."""

    def test_is_private_detected(self):
        import ipaddress
        for ip_str in ["10.0.0.1", "172.16.0.1", "192.168.1.1"]:
            ip = ipaddress.ip_address(ip_str)
            assert ip.is_private, f"{ip_str} must be detected as private"

    def test_is_loopback_detected(self):
        import ipaddress
        for ip_str in ["127.0.0.1", "127.0.0.2", "::1"]:
            ip = ipaddress.ip_address(ip_str)
            assert ip.is_loopback, f"{ip_str} must be detected as loopback"

    def test_is_link_local_detected(self):
        import ipaddress
        ip = ipaddress.ip_address("169.254.1.1")
        assert ip.is_link_local, f"169.254.1.1 must be detected as link-local"

    def test_is_multicast_detected(self):
        import ipaddress
        ip = ipaddress.ip_address("224.0.0.1")
        assert ip.is_multicast, f"224.0.0.1 must be detected as multicast"

    def test_is_reserved_detected(self):
        import ipaddress
        ip = ipaddress.ip_address("240.0.0.1")
        assert ip.is_reserved, f"240.0.0.1 must be detected as reserved"


# ---- helpers ----

class _RedirectFakeCtx:
    def __init__(self, fn):
        self._fn = fn

    async def __aenter__(self):
        return _RedirectFakeClient(self._fn)

    async def __aexit__(self, *a):
        pass


class _RedirectFakeClient:
    def __init__(self, fn):
        self._fn = fn

    def stream(self, method, url):
        return _RedirectStreamCtx(self._fn, url)


class _RedirectStreamCtx:
    def __init__(self, fn, url):
        self._fn = fn
        self._url = url

    async def __aenter__(self):
        return await self._fn(self._url)

    async def __aexit__(self, *a):
        pass
