import hashlib
import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

PDF_MAGIC = b"%PDF-"


class PdfDownloadError(Exception):
    def __init__(self, code: str, message: str, status_code: int | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code


async def download_pdf(url: str) -> tuple[bytes, str]:
    max_size = settings.PDF_MAX_SIZE_MB * 1024 * 1024

    async with httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=settings.PDF_MAX_REDIRECTS,
        timeout=httpx.Timeout(
            connect=settings.PDF_CONNECT_TIMEOUT_SECONDS,
            read=settings.PDF_DOWNLOAD_TIMEOUT_SECONDS,
            pool=settings.PDF_DOWNLOAD_TIMEOUT_SECONDS,
        ),
    ) as client:
        response = await client.get(url)
        status = response.status_code

        if status == 403:
            raise PdfDownloadError("PDF_FORBIDDEN", "PDF URL returned 403 Forbidden", status)
        if status == 404:
            raise PdfDownloadError("PDF_NOT_FOUND", "PDF URL returned 404 Not Found", status)
        if status >= 400:
            raise PdfDownloadError(
                "PDF_DOWNLOAD_FAILED", f"HTTP {status}", status
            )

        content_type = response.headers.get("content-type", "")
        content = response.read()

        if len(content) > max_size:
            raise PdfDownloadError(
                "PDF_TOO_LARGE",
                f"PDF size {len(content)} exceeds limit {max_size}",
            )

        if not content.startswith(PDF_MAGIC):
            if "text/html" in content_type:
                raise PdfDownloadError(
                    "PDF_NOT_PDF", "Content is HTML, not PDF"
                )
            raise PdfDownloadError(
                "PDF_INVALID_HEADER", "File does not start with %PDF-"
            )

        sha256 = hashlib.sha256(content).hexdigest()
        logger.info(
            "PDF downloaded url=%s size=%s sha256=%s",
            url[:120], len(content), sha256,
        )
        return content, sha256
