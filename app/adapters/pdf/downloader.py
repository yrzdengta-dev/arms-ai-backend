import hashlib
import logging
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass
from typing import cast
from urllib.parse import urljoin, urlparse

import httpcore
import httpx
from httpcore._backends.base import SOCKET_OPTION

from app.adapters.pdf.url_validator import (
    UnsafeURLException,
    resolve_hostname_public_ips,
    validate_pdf_url,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

PDF_MAGIC = b"%PDF-"


@dataclass
class DownloadResult:
    """Outcome of a PDF download attempt.

    Fields:
        content: Raw bytes of the downloaded file.
        sha256:  SHA-256 hex digest (empty string for non-PDF files, since
                 they are not uploaded to MinIO and don't need dedup).
        is_pdf:  True if the file starts with the %PDF- magic bytes.
        content_type: HTTP Content-Type header value (may be None).
        size_bytes: Length of content in bytes.
    """
    content: bytes
    sha256: str
    is_pdf: bool
    content_type: str | None
    size_bytes: int


class PdfDownloadError(Exception):
    def __init__(self, code: str, message: str, status_code: int | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code


async def download_pdf(url: str) -> DownloadResult:
    """Download a PDF from a URL with SSRF protection.

    - Validates the initial URL and every redirect target
    - Resolves DNS once per hop and pins to public IPs via per-transport
      NetworkBackend (no global monkeypatching)
    - Size limit, file header check, SHA-256 hashing
    - Non-PDF content is NOT an error — is_pdf=False is returned instead
    """
    url = validate_pdf_url(url)
    max_size = settings.PDF_MAX_SIZE_MB * 1024 * 1024
    max_redirects = settings.PDF_MAX_REDIRECTS

    timeout = httpx.Timeout(
        connect=settings.PDF_CONNECT_TIMEOUT_SECONDS,
        read=settings.PDF_DOWNLOAD_TIMEOUT_SECONDS,
        write=settings.PDF_DOWNLOAD_TIMEOUT_SECONDS,
        pool=settings.PDF_DOWNLOAD_TIMEOUT_SECONDS,
    )

    try:
        redirect_count = 0
        current_url = url

        while True:
            # Per-hop: resolve DNS, validate IPs, create isolated transport
            transport = await _make_pinned_transport(current_url)

            async with httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                timeout=timeout,
            ) as client, client.stream("GET", current_url) as response:
                    status = response.status_code

                    if status in (301, 302, 303, 307, 308):
                        redirect_count += 1
                        if redirect_count > max_redirects:
                            raise PdfDownloadError(
                                "PDF_TOO_MANY_REDIRECTS",
                                f"Exceeded max redirects ({max_redirects})",
                            )
                        location = response.headers.get("Location", "")
                        if not location:
                            raise PdfDownloadError(
                                "PDF_REDIRECT_NO_LOCATION",
                                f"Redirect status {status} with no Location header",
                            )
                        next_url = urljoin(current_url, location)
                        current_url = validate_pdf_url(next_url)
                        logger.debug(
                            "Following redirect %d to %s",
                            redirect_count,
                            current_url[:120],
                        )
                        continue

                    if status == 403:
                        raise PdfDownloadError(
                            "PDF_FORBIDDEN", "PDF URL returned 403 Forbidden", status
                        )
                    if status == 404:
                        raise PdfDownloadError(
                            "PDF_NOT_FOUND", "PDF URL returned 404 Not Found", status
                        )
                    if status >= 400:
                        raise PdfDownloadError(
                            "PDF_DOWNLOAD_FAILED", f"HTTP {status}", status
                        )

                    content = await _read_pdf_response_limited(response, max_size)
                    content_type = response.headers.get("content-type", "")
                    is_pdf = content.startswith(PDF_MAGIC)
                    sha256 = hashlib.sha256(content).hexdigest() if is_pdf else ""

                    logger.info(
                        "Downloaded url=%s size=%s is_pdf=%s content_type=%s",
                        url[:120],
                        len(content),
                        is_pdf,
                        content_type,
                    )
                    return DownloadResult(
                        content=content,
                        sha256=sha256,
                        is_pdf=is_pdf,
                        content_type=content_type or None,
                        size_bytes=len(content),
                    )

    except PdfDownloadError:
        raise
    except UnsafeURLException as e:
        raise PdfDownloadError("PDF_UNSAFE_URL", str(e)) from e
    except httpx.ConnectTimeout as e:
        raise PdfDownloadError(
            "PDF_CONNECT_TIMEOUT", "Connection timed out while downloading PDF"
        ) from e
    except httpx.ReadTimeout as e:
        raise PdfDownloadError(
            "PDF_READ_TIMEOUT", "Read timed out while downloading PDF"
        ) from e
    except httpx.WriteTimeout as e:
        raise PdfDownloadError(
            "PDF_WRITE_TIMEOUT", "Write timed out while downloading PDF"
        ) from e
    except httpx.PoolTimeout as e:
        raise PdfDownloadError(
            "PDF_POOL_TIMEOUT", "Connection pool timed out while downloading PDF"
        ) from e
    except httpx.TimeoutException as e:
        raise PdfDownloadError(
            "PDF_TIMEOUT", "Request timed out while downloading PDF"
        ) from e
    except httpx.RequestError as e:
        raise PdfDownloadError("PDF_NETWORK_ERROR", f"Network error: {e}") from e


async def _read_pdf_response_limited(response: httpx.Response, max_size: int) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = None
        if declared_size is not None and declared_size > max_size:
            raise PdfDownloadError(
                "PDF_TOO_LARGE",
                f"PDF Content-Length {declared_size} exceeds limit {max_size}",
            )

    content = bytearray()
    async for chunk in response.aiter_bytes():
        content.extend(chunk)
        if len(content) > max_size:
            raise PdfDownloadError(
                "PDF_TOO_LARGE",
                f"PDF size exceeds limit {max_size}",
            )
    return bytes(content)


async def _make_pinned_transport(url: str) -> "_PinnedTransport":
    """Create an httpx transport with DNS pinned for the URL's hostname.

    Resolves hostname via resolve_hostname_public_ips, validates all IPs,
    then creates an httpx-compatible transport whose network backend routes
    TCP connections for this hostname to the validated public IPs. TLS SNI
    and Host header continue using the original hostname (preserved by httpcore).

    Each transport is independent — concurrent downloads to different hosts
    do not share or interfere with each other's DNS state.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise PdfDownloadError("PDF_BAD_URL", "URL has no hostname")

    public_infos = await resolve_hostname_public_ips(hostname, parsed.port or 443)
    pinned_ip = public_infos[0][4][0]

    return _PinnedTransport(hostname, pinned_ip)


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    """Network backend that pins a specific hostname to a pre-validated IP.

    Delegates all operations to the default AutoBackend (asyncio/anyio).
    Only overrides connect_tcp to substitute the pinned IP — TLS SNI and
    certificate verification continue using the original hostname.

    This is the httpcore-correct injection point for per-request DNS pinning.
    No global state (socket.getaddrinfo, loop resolver) is modified.
    """

    def __init__(self, hostname: str, ip: str) -> None:
        self._hostname = hostname
        self._ip = ip
        from httpcore._backends.auto import AutoBackend
        self._inner = AutoBackend()

    async def connect_tcp(
        self, host: str, port: int, timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        # Route this hostname to the pinned IP instead of doing a fresh DNS lookup
        if host == self._hostname:
            host = self._ip
        return await self._inner.connect_tcp(
            host, port, timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._inner.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options,
        )

    async def sleep(self, seconds: float) -> None:
        return await self._inner.sleep(seconds)


class _PinnedTransport(httpx.AsyncBaseTransport):
    """httpx-compatible transport that wraps an httpcore pool with DNS pinning.

    httpx 0.28.x does not convert between its own Request/Response types
    and httpcore's when a raw httpcore transport is passed. This wrapper
    performs the conversion so httpx can use our pinned pool directly.

    Uses httpx public API types only (AsyncBaseTransport, AsyncByteStream).
    No dependency on httpx._transports.default private module.
    """

    def __init__(self, hostname: str, pinned_ip: str) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            network_backend=_PinnedBackend(hostname, pinned_ip),
        )

    async def handle_async_request(
        self, request: httpx.Request,
    ) -> httpx.Response:
        req = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        resp = await self._pool.handle_async_request(req)

        return httpx.Response(
            status_code=resp.status,
            headers=resp.headers,
            stream=_ByteStream(cast("AsyncIterable[bytes]", resp.stream)),
            extensions=resp.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


class _ByteStream(httpx.AsyncByteStream):
    """Wraps an httpcore async byte stream for httpx compatibility.

    Implements httpx.AsyncByteStream (public API) so httpx._client can
    assert isinstance(response.stream, AsyncByteStream).
    """

    def __init__(self, stream: AsyncIterable[bytes]) -> None:
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        if hasattr(self._stream, "aclose"):
            await self._stream.aclose()
