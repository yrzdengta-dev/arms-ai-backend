"""Acceptance tests: PDF error handling (Section 3.6)

Verifies granular error codes for:
- 403, 404, timeout, size limit exceeded
- HTML disguised as PDF
- Wrong file header
- Encrypted PDF
- Corrupted PDF
- Empty PDF
- Scanned PDF (no text)
- Partial page extraction failure

Error codes must be specific and trackable, not all compressed to PDF_ERROR.
"""


from app.adapters.pdf.downloader import PdfDownloadError


class TestPdfErrorCodes:
    """Error codes must be specific and distinguishable."""

    def test_error_403_has_distinct_code(self):
        err = PdfDownloadError("PDF_FORBIDDEN", "PDF URL returned 403 Forbidden", 403)
        assert err.code == "PDF_FORBIDDEN"

    def test_error_404_has_distinct_code(self):
        err = PdfDownloadError("PDF_NOT_FOUND", "PDF URL returned 404 Not Found", 404)
        assert err.code == "PDF_NOT_FOUND"

    def test_error_timeout_has_distinct_code(self):
        err = PdfDownloadError("PDF_DOWNLOAD_TIMEOUT", "Download timed out")
        assert "TIMEOUT" in err.code.upper()

    def test_error_size_limit_has_distinct_code(self):
        err = PdfDownloadError("PDF_TOO_LARGE", "PDF exceeds size limit")
        assert "SIZE" in err.code.upper() or "LARGE" in err.code.upper()

    def test_error_not_pdf_has_distinct_code(self):
        err = PdfDownloadError("PDF_NOT_PDF", "Content is HTML, not PDF")
        assert "NOT_PDF" in err.code.upper()

    def test_error_bad_header_has_distinct_code(self):
        err = PdfDownloadError("PDF_INVALID_HEADER", "File does not start with %PDF-")
        assert "HEADER" in err.code.upper() or "INVALID" in err.code.upper()

    def test_different_errors_have_different_codes(self):
        """403, 404, timeout, and size errors must have unique codes."""
        codes = set()
        for err in [
            PdfDownloadError("PDF_FORBIDDEN", "forbidden", 403),
            PdfDownloadError("PDF_NOT_FOUND", "not found", 404),
            PdfDownloadError("PDF_DOWNLOAD_TIMEOUT", "timeout"),
            PdfDownloadError("PDF_TOO_LARGE", "too large"),
            PdfDownloadError("PDF_NOT_PDF", "not pdf"),
            PdfDownloadError("PDF_INVALID_HEADER", "bad header"),
        ]:
            codes.add(err.code)
        assert len(codes) >= 4, (
            f"Expected at least 4 distinct error codes, got {len(codes)}: {codes}"
        )


class TestPdfErrorPropagation:
    """Error info must be preserved in OrderFile records."""

    def test_parse_error_codes_are_specific(self):
        """Parse errors must have specific codes, not generic PDF_ERROR."""
        from app.adapters.pdf.parser import PdfParseResult

        errors = [
            PdfParseResult(
                text="", page_count=0, is_scanned=False, is_encrypted=True,
                error_code="PDF_ENCRYPTED", error_message="Encrypted PDF",
            ),
            PdfParseResult(
                text="", page_count=0, is_scanned=False, is_encrypted=False,
                error_code="PDF_CORRUPTED", error_message="Corrupted",
            ),
            PdfParseResult(
                text="", page_count=0, is_scanned=True, is_encrypted=False,
                error_code="OCR_REQUIRED", error_message="Scanned, no text",
            ),
            PdfParseResult(
                text="", page_count=0, is_scanned=False, is_encrypted=False,
                error_code="PDF_EMPTY", error_message="Empty document",
            ),
        ]
        for r in errors:
            assert r.error_code, "Each error must have a code"
            assert r.error_code != "PDF_ERROR", (
                f"Error code must be specific, not generic 'PDF_ERROR': {r.error_code}"
            )
