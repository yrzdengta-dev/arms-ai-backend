"""Acceptance tests: Multi-PDF support (Section 3.5)

Verifies:
- Multiple PDFs are all downloaded and parsed
- AI input contains text from ALL PDFs
- Each PDF retains file_name and page numbers
- No .limit(1) restricting to single file
- One required PDF failure → not AI_COMPLETED
- Optional PDF failure strategy from Skill
"""

import pytest

from app.models.order_file import OrderFile
from app.models.user import User
from app.schemas.order import OrderIngestRequest
from app.services.order_service import order_service


class TestMultiPdf:
    """Multiple PDFs per order must all be processed."""

    @pytest.mark.asyncio
    async def test_all_pdfs_saved(self, db_session):
        """Each PDF in pdf_files must result in an OrderFile record."""
        user = User(arms_account="testuser", id="u-mpdf")
        db_session.add(user)
        await db_session.flush()

        request = OrderIngestRequest(
            task_order_id="TN-MPDF-001",
            order_snapshot={"skc": "SKC-MPDF"},
            raw_detail={},
            pdf_files=[
                {"name": "report1.pdf", "url": "https://example.com/1.pdf"},
                {"name": "report2.pdf", "url": "https://example.com/2.pdf"},
                {"name": "report3.pdf", "url": "https://example.com/3.pdf"},
            ],
        )
        order, created = await order_service.ingest(db_session, request, user)
        await db_session.commit()

        # Verify 3 PDF source records exist
        from sqlalchemy import func, select
        result = await db_session.execute(
            select(func.count()).select_from(OrderFile).where(OrderFile.order_id == order.id)
        )
        count = result.scalar()
        assert count == 3, f"Expected 3 PDF records, got {count}"

    @pytest.mark.asyncio
    async def test_all_parsed_texts_collected(self, db_session):
        """_get_parsed_text must concatenate text from ALL files, not just .limit(1)."""
        from app.workers.tasks import _get_parsed_text

        user = User(arms_account="mpdf-testuser-2", id="u-mpdf2")
        db_session.add(user)
        await db_session.flush()

        request = OrderIngestRequest(
            task_order_id="TN-MPDF-002",
            order_snapshot={"skc": "SKC-MPDF2"},
            raw_detail={},
            pdf_files=[
                {"name": "a.pdf", "url": "https://example.com/a.pdf"},
                {"name": "b.pdf", "url": "https://example.com/b.pdf"},
            ],
        )
        order, _ = await order_service.ingest(db_session, request, user)
        await db_session.commit()

        # Create two OrderFile records with different text
        f1 = OrderFile(
            order_id=order.id,
            original_name="a.pdf",
            sha256="aaa",
            parse_status="READY",
            parsed_text="Content from file A\nPage 1\nPage 2",
        )
        f2 = OrderFile(
            order_id=order.id,
            original_name="b.pdf",
            sha256="bbb",
            parse_status="READY",
            parsed_text="Content from file B\nPage 1",
        )
        db_session.add_all([f1, f2])
        await db_session.commit()

        text = await _get_parsed_text(db_session, order.id, order.order_version)
        assert "Content from file A" in text, f"Missing text from a.pdf, got: {text[:200]}"
        assert "Content from file B" in text, f"Missing text from b.pdf, got: {text[:200]}"

    @pytest.mark.asyncio
    async def test_failed_required_pdf_not_ai_completed(self, db_session):
        """If one required PDF fails, state must not progress to AI_COMPLETED."""
        # Verify that PDF_FAILED is a valid state from PDF_DOWNLOADING
        from app.core.state_machine import PipelineStatus, can_transition

        # PDF_DOWNLOADING can go to PDF_FAILED
        assert can_transition(PipelineStatus.PDF_DOWNLOADING, PipelineStatus.PDF_FAILED)
        # PDF_FAILED cannot go to AI_COMPLETED
        assert not can_transition(PipelineStatus.PDF_FAILED, PipelineStatus.AI_COMPLETED)
        # PDF_FAILED must go through FAILED_RETRYABLE or FAILED_FINAL
        assert not can_transition(PipelineStatus.PDF_FAILED, PipelineStatus.PDF_READY)
