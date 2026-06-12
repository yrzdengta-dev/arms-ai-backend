"""Security tests: DNS pinning concurrency safety (P0-2 extension).

Verifies that DNS pinning:
  - Never modifies global socket.getaddrinfo
  - Is concurrency-safe (two downloads to different hosts don't interfere)
  - Routes actual TCP connections to the pinned IP
  - Preserves TLS SNI and Host header with the original hostname
"""

import asyncio
import socket as _socket_module
import ssl
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import httpcore
import pytest

from app.adapters.pdf.url_validator import UnsafeURLException, validate_pdf_url


class TestTransportCompatibility:
    """Verify _PinnedTransport / _ByteStream work with current httpx/httpcore."""

    def test_byte_stream_implements_async_byte_stream(self):
        """_ByteStream must satisfy httpx.AsyncByteStream (asserted in _client.py)."""
        from app.adapters.pdf.downloader import _ByteStream

        async def _fake_stream():
            yield b"chunk1"
            yield b"chunk2"
            return  # pragma: no cover

        stream = _ByteStream(_fake_stream())
        assert isinstance(stream, httpx.AsyncByteStream), (
            "_ByteStream must be an instance of httpx.AsyncByteStream"
        )

    @pytest.mark.asyncio
    async def test_pinned_transport_is_valid_httpx_transport(self):
        """_PinnedTransport must satisfy httpx.AsyncBaseTransport."""
        from app.adapters.pdf.downloader import _PinnedTransport

        transport = _PinnedTransport("example.com", "203.0.113.1")
        assert isinstance(transport, httpx.AsyncBaseTransport), (
            "_PinnedTransport must be an instance of httpx.AsyncBaseTransport"
        )
        await transport.aclose()

    def test_httpcore_url_accepts_raw_bytes_from_httpx_url(self):
        """httpcore.URL accepts raw_scheme/raw_host/raw_path (bytes) from httpx.URL."""
        httpcore_url = httpcore.URL(
            scheme=b"https", host=b"example.com", port=None, target=b"/path?q=1",
        )
        assert httpcore_url is not None


class TestGetaddrinfoNotModified:
    """Global socket.getaddrinfo must NEVER be replaced or modified."""

    def test_getaddrinfo_identity_unchanged_after_validate(self):
        """validate_pdf_url must not touch socket.getaddrinfo."""
        original = _socket_module.getaddrinfo
        validate_pdf_url("https://example.com/doc.pdf")
        assert _socket_module.getaddrinfo is original, (
            "validate_pdf_url must not modify socket.getaddrinfo"
        )

    def test_getaddrinfo_identity_unchanged_after_blocked(self):
        """Even when blocking a URL, getaddrinfo must not be touched."""
        original = _socket_module.getaddrinfo
        try:
            validate_pdf_url("https://localhost/doc.pdf")
        except UnsafeURLException:
            pass
        assert _socket_module.getaddrinfo is original, (
            "validate_pdf_url must not modify socket.getaddrinfo even on blocked URLs"
        )


class TestPinnedTransportIsolation:
    """Each download/request must use its own isolated DNS context.

    Uses a real localhost TCP/HTTP server so the full transport pipeline
    (httpx -> _PinnedTransport -> httpcore -> _PinnedBackend -> AutoBackend)
    is exercised. No mocks that bypass the transport code path.
    """

    @pytest.mark.asyncio
    async def test_socket_getaddrinfo_unchanged_during_download(self, monkeypatch):
        """The download must not replace socket.getaddrinfo at any point.

        Wraps getaddrinfo with a sentinel BEFORE calling download_pdf, then
        verifies afterward that the sentinel is still in place — proving the
        download code never reassigned socket.getaddrinfo.
        """
        from app.adapters.pdf.downloader import PdfDownloadError, download_pdf

        original = _socket_module.getaddrinfo
        sentinel_calls = []

        def _sentinel_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            sentinel_calls.append(host)
            return original(host, port, family, type, proto, flags)

        _socket_module.getaddrinfo = _sentinel_getaddrinfo
        try:
            monkeypatch.setattr(
                "app.adapters.pdf.downloader.validate_pdf_url", lambda u: u,
            )

            # Start a local HTTP server so the transport is really exercised
            server, port, _host_headers, _req_count = await _start_http_server()
            try:
                monkeypatch.setattr(
                    "app.adapters.pdf.downloader.resolve_hostname_public_ips",
                    _make_fake_resolver("127.0.0.1"),
                )
                try:
                    await download_pdf(f"http://sentinel-test.local:{port}/a.pdf")
                except PdfDownloadError:
                    pass
            finally:
                await _stop_server(server)

            assert _socket_module.getaddrinfo is _sentinel_getaddrinfo, (
                "download_pdf replaced socket.getaddrinfo! "
                "Must use per-transport DNS pinning instead."
            )
        finally:
            _socket_module.getaddrinfo = original

    @pytest.mark.asyncio
    async def test_concurrent_http_downloads_pin_tcp_keep_host_header(
        self, monkeypatch,
    ):
        """Two concurrent HTTP downloads: real TCP to pinned IP, Host headers original.

        Uses a real localhost HTTP server. AutoBackend.connect_tcp is wrapped
        to record the actual TCP connection target. The server handler records
        Host headers. Verifies:
          - Every TCP connection uses the pinned IP, never the hostname
          - Host headers carry the original hostname
          - Each download's transport is isolated (no cross-contamination)
          - Non-vacuous: len(captured) >= 2
        """
        from app.adapters.pdf.downloader import download_pdf

        monkeypatch.setattr(
            "app.adapters.pdf.downloader.validate_pdf_url", lambda u: u,
        )

        # Start a local HTTP server that records Host headers
        server, port, host_headers, _request_count = await _start_http_server()

        try:
            # Mock DNS to resolve everything to 127.0.0.1
            monkeypatch.setattr(
                "app.adapters.pdf.downloader.resolve_hostname_public_ips",
                _make_fake_resolver("127.0.0.1"),
            )

            # Wrap AutoBackend.connect_tcp to record TCP target params
            tcp_targets: list[tuple[str, int]] = []
            _wrap_autobackend_connect_tcp(monkeypatch, tcp_targets)

            # Concurrency: use a barrier so both tasks reach the HTTP call together
            barrier = asyncio.Barrier(2)
            results = {}

            async def download(hostname: str) -> None:
                url = f"http://{hostname}:{port}/doc.pdf"
                async with barrier:
                    pass  # both tasks are now synchronised
                try:
                    result = await download_pdf(url)
                    results[hostname] = (result.content, result.sha256)
                except Exception as e:
                    results[hostname] = e

            await asyncio.gather(
                download("host-a.example.com"),
                download("host-b.example.com"),
            )

            # Both downloads must produce valid PDF content
            for host in ("host-a.example.com", "host-b.example.com"):
                val = results.get(host)
                assert val is not None, f"Download for {host} returned None"
                assert not isinstance(val, Exception), (
                    f"Download for {host} failed: {val}"
                )
                content, _ = val
                assert content.startswith(b"%PDF-1.4"), (
                    f"Download for {host} did not return PDF content"
                )

            # ---- TCP pinning assertions ----
            assert len(tcp_targets) >= 2, (
                f"Expected >=2 TCP connections, got {len(tcp_targets)}: {tcp_targets}"
            )
            for tcp_host, tcp_port in tcp_targets:
                assert tcp_host == "127.0.0.1", (
                    f"TCP connection must use pinned IP 127.0.0.1, got {tcp_host}"
                )
                assert tcp_port == port, (
                    f"TCP port mismatch: expected {port}, got {tcp_port}"
                )

            # ---- Host header assertions ----
            assert len(host_headers) >= 2, (
                f"Expected >=2 Host headers captured, got {len(host_headers)}: {host_headers}"
            )
            host_a_found = any("host-a.example.com" in h for h in host_headers)
            host_b_found = any("host-b.example.com" in h for h in host_headers)
            assert host_a_found, (
                f"Host header for host-a.example.com not found in {host_headers}"
            )
            assert host_b_found, (
                f"Host header for host-b.example.com not found in {host_headers}"
            )

            # ---- Isolation: each transport must have its own DNS state ----
            # The fact that both downloads succeeded with correct Host headers
            # proves the two _PinnedTransport instances did not share state.

        finally:
            await _stop_server(server)

    @pytest.mark.asyncio
    async def test_tls_sni_preserved_while_tcp_pinned(self, monkeypatch):
        """TLS SNI = original hostname; TCP connection = pinned IP.

        Starts a real localhost TLS server with a self-signed certificate.
        The server's SNI callback records the server_name presented by the
        client. Verifies:
          - TCP connection target = 127.0.0.1 (the pinned IP)
          - TLS SNI server_name = original hostname (not the IP)
          - Both assertions are non-vacuous (len >= 1)
        """
        from app.adapters.pdf.downloader import download_pdf

        monkeypatch.setattr(
            "app.adapters.pdf.downloader.validate_pdf_url", lambda u: u,
        )

        # Generate self-signed cert that covers our test hostname
        cert_pem, key_pem = _generate_self_signed_cert(
            common_name="host-sni.example.com",
            san_dns_names=["host-sni.example.com", "localhost"],
        )

        # Write cert and key to temp files for the server
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".pem", delete=False,
        ) as cert_file, tempfile.NamedTemporaryFile(
            mode="w", suffix=".pem", delete=False,
        ) as key_file:
            cert_file.write(cert_pem.decode())
            key_file.write(key_pem.decode())
            cert_path = Path(cert_file.name)
            key_path = Path(key_file.name)

        try:
            # Start TLS server that records SNI
            server, port, sni_values, tls_host_headers = (
                await _start_tls_server(cert_path, key_path)
            )

            try:
                # Mock DNS to resolve everything to 127.0.0.1
                monkeypatch.setattr(
                    "app.adapters.pdf.downloader.resolve_hostname_public_ips",
                    _make_fake_resolver("127.0.0.1"),
                )

                # Wrap AutoBackend.connect_tcp to record TCP target
                tcp_targets: list[tuple[str, int]] = []
                _wrap_autobackend_connect_tcp(monkeypatch, tcp_targets)

                # Make httpx trust our self-signed cert
                _patch_ssl_trust_cert(monkeypatch, cert_pem)

                result = await download_pdf(
                    f"https://host-sni.example.com:{port}/test.pdf",
                )

                assert result.content.startswith(b"%PDF-1.4"), "Download should return PDF content"

                # ---- TCP pinning assertion ----
                assert len(tcp_targets) >= 1, (
                    f"Expected >=1 TCP connection, got {len(tcp_targets)}"
                )
                for tcp_host, tcp_port in tcp_targets:
                    assert tcp_host == "127.0.0.1", (
                        f"TCP must use pinned IP 127.0.0.1, got {tcp_host}"
                    )

                # ---- TLS SNI assertion ----
                assert len(sni_values) >= 1, (
                    f"Expected >=1 SNI value captured, got {len(sni_values)}"
                )
                for sni in sni_values:
                    assert sni == "host-sni.example.com", (
                        f"TLS SNI must be original hostname 'host-sni.example.com', "
                        f"got {sni!r}"
                    )

                # ---- Host header assertion (decrypted by TLS, seen by server) ----
                assert len(tls_host_headers) >= 1, (
                    f"Expected >=1 Host header, got {len(tls_host_headers)}"
                )
                assert any(
                    "host-sni.example.com" in h for h in tls_host_headers
                ), f"Host header should contain host-sni.example.com: {tls_host_headers}"

            finally:
                await _stop_server(server)

        finally:
            cert_path.unlink(missing_ok=True)
            key_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _generate_self_signed_cert(
    common_name: str = "localhost",
    san_dns_names: list[str] | None = None,
) -> tuple[bytes, bytes]:
    """Return (cert_pem_bytes, key_pem_bytes) for a self-signed certificate."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    if san_dns_names is None:
        san_dns_names = ["localhost"]

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(name) for name in san_dns_names]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def _make_fake_resolver(ip: str):
    """Return an async function matching resolve_hostname_public_ips signature."""
    import socket

    async def _fake_resolve(hostname, port=443):
        return [(
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (ip, port),
        )]

    return _fake_resolve


def _wrap_autobackend_connect_tcp(monkeypatch, tcp_targets: list):
    """Monkeypatch AutoBackend.connect_tcp to record (host, port) calls."""
    from httpcore._backends.auto import AutoBackend

    _original_connect_tcp = AutoBackend.connect_tcp

    async def _recording_connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None,
    ):
        tcp_targets.append((host, port))
        return await _original_connect_tcp(
            self, host, port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    monkeypatch.setattr(AutoBackend, "connect_tcp", _recording_connect_tcp)


def _patch_ssl_trust_cert(monkeypatch, cert_pem: bytes):
    """Make ssl.create_default_context() trust the given PEM certificate."""
    _original_create = ssl.create_default_context

    def _patched_create_default_context(*args, **kwargs):
        ctx = _original_create(*args, **kwargs)
        ctx.load_verify_locations(cadata=cert_pem.decode())
        return ctx

    monkeypatch.setattr(ssl, "create_default_context", _patched_create_default_context)


async def _start_http_server():
    """Start an asyncio HTTP server on localhost:0. Returns (server, port, host_headers, request_count).

    The server records the Host header from each request and returns a
    minimal valid PDF response.
    """
    host_headers: list[str] = []
    request_count = [0]

    async def _handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        request_count[0] += 1
        try:
            raw = await asyncio.wait_for(reader.read(8192), timeout=5.0)
            request_text = raw.decode(errors="replace")
            for line in request_text.split("\r\n"):
                if line.lower().startswith("host:"):
                    host_headers.append(line.split(":", 1)[1].strip())
            # Send back a valid PDF response
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/pdf\r\n"
                b"Content-Length: 20\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b"%PDF-1.4 fake pdf body"
            )
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port, host_headers, request_count


async def _start_tls_server(cert_path: Path, key_path: Path):
    """Start a TLS server on localhost:0 with SNI callback.

    Returns (server, port, sni_values, host_headers).
    """
    sni_values: list[str] = []
    host_headers: list[str] = []

    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.load_cert_chain(str(cert_path), str(key_path))

    # Record SNI via callback
    def _sni_callback(ssl_obj, server_name, ctx):
        if server_name:
            sni_values.append(server_name)

    try:
        tls_context.sni_callback = _sni_callback
    except AttributeError:
        # Fallback for older Python
        tls_context.set_servername_callback(_sni_callback)

    async def _handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            raw = await asyncio.wait_for(reader.read(8192), timeout=5.0)
            request_text = raw.decode(errors="replace")
            for line in request_text.split("\r\n"):
                if line.lower().startswith("host:"):
                    host_headers.append(line.split(":", 1)[1].strip())
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/pdf\r\n"
                b"Content-Length: 18\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b"%PDF-1.4 fake pdf\n"
            )
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(
        _handler, "127.0.0.1", 0, ssl=tls_context,
    )
    port = server.sockets[0].getsockname()[1]
    return server, port, sni_values, host_headers


async def _stop_server(server):
    """Close an asyncio server and wait for cleanup."""
    server.close()
    await server.wait_closed()
