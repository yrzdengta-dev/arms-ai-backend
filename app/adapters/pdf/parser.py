import logging
from dataclasses import dataclass
from typing import Protocol

from pypdf import PdfReader

logger = logging.getLogger(__name__)


@dataclass
class PdfParseResult:
    text: str
    page_count: int
    is_scanned: bool
    is_encrypted: bool
    error_code: str | None = None
    error_message: str | None = None


class PdfParser(Protocol):
    async def parse(self, content: bytes, filename: str) -> PdfParseResult:
        ...


class PyPdfParser:
    async def parse(self, content: bytes, filename: str) -> PdfParseResult:
        try:
            from io import BytesIO

            reader = PdfReader(BytesIO(content))

            if reader.is_encrypted:
                return PdfParseResult(
                    text="", page_count=0, is_scanned=False, is_encrypted=True,
                    error_code="PDF_ENCRYPTED", error_message="PDF is encrypted",
                )

            page_count = len(reader.pages)
            if page_count == 0:
                return PdfParseResult(
                    text="", page_count=0, is_scanned=False, is_encrypted=False,
                    error_code="PDF_EMPTY", error_message="PDF has no pages",
                )

            texts: list[str] = []
            for page in reader.pages:
                try:
                    t = page.extract_text()
                    if t:
                        texts.append(t)
                except Exception:
                    logger.warning("Failed to extract text from page in %s", filename)

            full_text = "\n".join(texts).strip()
            is_scanned = len(full_text) < 20

            if is_scanned:
                return PdfParseResult(
                    text=full_text, page_count=page_count, is_scanned=True,
                    is_encrypted=False,
                    error_code="OCR_REQUIRED", error_message="PDF appears to be scanned, no extractable text",
                )

            return PdfParseResult(
                text=full_text, page_count=page_count, is_scanned=False, is_encrypted=False,
            )

        except Exception as e:
            logger.exception("PDF parse error file=%s", filename)
            return PdfParseResult(
                text="", page_count=0, is_scanned=False, is_encrypted=False,
                error_code="PDF_PARSE_ERROR", error_message=str(e),
            )
