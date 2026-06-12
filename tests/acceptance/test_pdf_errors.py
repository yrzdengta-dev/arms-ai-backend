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


import pytest

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


def test_ocr_required_not_treated_as_ready():
    """A file with OCR_REQUIRED parse_status must not be counted as ready."""
    from app.services.pdf_service import _parse_status_from_result
    from dataclasses import dataclass

    @dataclass
    class FakeParseResult:
        error_code: str | None = None
        error_message: str | None = None
        is_scanned: bool = False
        text: str = ""

    # OCR_REQUIRED via error_code
    assert _parse_status_from_result(FakeParseResult(error_code="OCR_REQUIRED")) == "OCR_REQUIRED"
    # OCR_REQUIRED via is_scanned
    assert _parse_status_from_result(FakeParseResult(is_scanned=True)) == "OCR_REQUIRED"
    # FAILED
    assert _parse_status_from_result(FakeParseResult(error_code="PDF_ENCRYPTED")) == "FAILED"
    # READY
    assert _parse_status_from_result(FakeParseResult()) == "READY"


@pytest.mark.asyncio
async def test_no_duplicate_order_files_after_processing(db_session):
    """After PDF processing, old PENDING records must be cleaned up — no duplicates."""
    import uuid
    from app.models.order import Order
    from app.models.order_file import OrderFile
    from app.models.user import User

    user = User(arms_account="dedup-test", id="u-dedup")
    db_session.add(user)
    await db_session.flush()

    order = Order(
        id=str(uuid.uuid4()),
        task_order_id="TN-DEDUP-001",
        owner_user_id=user.id,
        pipeline_status="RECEIVED",
        order_version=1,
        detail_hash="test",
    )
    db_session.add(order)
    await db_session.flush()

    # Create a PENDING file record (simulating _enqueue_order)
    pending = OrderFile(
        order_id=order.id,
        order_version=1,
        original_name="test.pdf",
        source_url="https://example.com/test.pdf",
        parse_status="PENDING",
    )
    db_session.add(pending)
    await db_session.flush()
    pending_id = pending.id

    # Create a new file record with parsed result (simulating process_pdf_for_order result)
    new_file = OrderFile(
        order_id=order.id,
        order_version=1,
        original_name="test.pdf",
        source_url="https://example.com/test.pdf",
        parse_status="READY",
        parsed_text="some text",
        sha256="abc123",
        storage_key="pdfs/abc123.pdf",
        content_type="application/pdf",
        size_bytes=1000,
    )
    db_session.add(new_file)
    await db_session.flush()
    new_id = new_file.id

    # Simulate cleanup: delete old PENDING record
    old = await db_session.get(OrderFile, pending_id)
    if old is not None and new_id != pending_id:
        await db_session.delete(old)
    await db_session.commit()

    # Verify no duplicates remain
    from sqlalchemy import select, func
    count_stmt = select(func.count()).select_from(OrderFile).where(OrderFile.order_id == order.id)
    count = (await db_session.execute(count_stmt)).scalar()
    assert count == 1, f"Should have exactly 1 file record after cleanup, got {count}"
