"""Unit tests: process_pdf_for_order SKIPPED handling (Slice 3).

Verifies that non-PDF files produce parse_status='SKIPPED' OrderFile records
with no storage_key / sha256, and preserved metadata.
"""

import pytest
from sqlalchemy import select

from app.adapters.pdf.downloader import DownloadResult
from app.models.order import Order
from app.models.order_file import OrderFile
from app.models.user import User
from app.services.pdf_service import process_pdf_for_order


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _mock_download(is_pdf, content_type, sha256="", content=b""):
    """Create an async mock for download_pdf returning a DownloadResult."""
    async def _mock(url):
        return DownloadResult(
            content=content or (b"%PDF-1.4 fake" if is_pdf else b"\x89PNG\r\n\x1a\nfake"),
            sha256=sha256 or ("abc123" if is_pdf else ""),
            is_pdf=is_pdf,
            content_type=content_type,
            size_bytes=len(content) if content else 15,
        )
    return _mock


def _mock_minio():
    """Return a fake storage object that accepts upload() calls."""
    class FakeStorage:
        async def upload(self, key, content):
            pass
    return FakeStorage()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProcessPdfForOrderSkipped:
    """Non-PDF files must create SKIPPED OrderFile records."""

    @pytest.mark.asyncio
    async def test_non_pdf_creates_skipped_record(self, db_session, monkeypatch):
        """is_pdf=False → parse_status='SKIPPED', error_code='NOT_PDF'."""
        user, order = await _setup_order(db_session, "TN-SKIP-001", "u-skip")
        monkeypatch.setattr(
            "app.services.pdf_service.download_pdf",
            _mock_download(is_pdf=False, content_type="image/png"),
        )

        file_record = await process_pdf_for_order(
            db_session, order.id, "https://example.com/img.png", "screenshot.png"
        )

        assert file_record.parse_status == "SKIPPED"
        assert file_record.error_code == "NOT_PDF"
        assert "image/png" in (file_record.error_message or "")
        assert file_record.parsed_text is None

    @pytest.mark.asyncio
    async def test_skipped_record_has_no_storage_or_sha256(self, db_session, monkeypatch):
        """SKIPPED files were not uploaded to MinIO — no storage_key, no sha256."""
        user, order = await _setup_order(db_session, "TN-SKIP-002", "u-skip2")
        monkeypatch.setattr(
            "app.services.pdf_service.download_pdf",
            _mock_download(is_pdf=False, content_type="image/jpeg"),
        )

        file_record = await process_pdf_for_order(
            db_session, order.id, "https://example.com/photo.jpg", "photo.jpg"
        )

        assert file_record.storage_key is None
        assert file_record.sha256 is None

    @pytest.mark.asyncio
    async def test_skipped_record_preserves_metadata(self, db_session, monkeypatch):
        """original_name, source_url, content_type, size_bytes must be preserved."""
        user, order = await _setup_order(db_session, "TN-SKIP-003", "u-skip3")
        monkeypatch.setattr(
            "app.services.pdf_service.download_pdf",
            _mock_download(is_pdf=False, content_type="text/plain", content=b"just text"),
        )

        file_record = await process_pdf_for_order(
            db_session, order.id, "https://example.com/notes.txt", "notes.txt"
        )

        assert file_record.original_name == "notes.txt"
        assert file_record.source_url == "https://example.com/notes.txt"
        assert file_record.content_type == "text/plain"
        assert file_record.size_bytes == 9


class TestProcessPdfForOrderStillWorks:
    """Valid PDFs must still produce READY / OCR_REQUIRED / FAILED records."""

    @pytest.mark.asyncio
    async def test_valid_pdf_still_processed(self, db_session, monkeypatch):
        """is_pdf=True → normal upload + parse flow (mocked)."""
        user, order = await _setup_order(db_session, "TN-OK-001", "u-ok")
        monkeypatch.setattr(
            "app.services.pdf_service.download_pdf",
            _mock_download(is_pdf=True, content_type="application/pdf", sha256="deadbeef"),
        )
        monkeypatch.setattr("app.services.pdf_service.minio_storage", _mock_minio())

        file_record = await process_pdf_for_order(
            db_session, order.id, "https://example.com/valid.pdf", "valid.pdf"
        )

        # Should not be SKIPPED — normal processing
        assert file_record.parse_status != "SKIPPED"
        assert file_record.storage_key is not None
        assert file_record.sha256 is not None
        assert file_record.content_type == "application/pdf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _setup_order(db_session, task_order_id: str, user_id: str) -> tuple[User, Order]:
    user = User(arms_account=task_order_id.lower(), id=user_id)
    order = Order(
        id=f"ord-{task_order_id}",
        task_order_id=task_order_id,
        owner_user_id=user.id,
        pipeline_status="PDF_DOWNLOADING",
        order_version=1,
        detail_hash="test",
    )
    db_session.add_all([user, order])
    await db_session.flush()
    return user, order
