"""Unit tests: _run_pdf_task counting logic (Slice 4).

Verifies the new grouping:
  READY        → processed_count
  SKIPPED      → not failed, not counted
  OCR_REQUIRED → not failed, not counted
  FAILED       → any_failed = True

Final state:
  any_failed                   → PDF_FAILED
  processed_count > 0          → PDF_READY
  processed_count == 0 + skip  → MANUAL_REQUIRED
"""

import pytest
from sqlalchemy import select

from app.core.state_machine import PipelineStatus
from app.models.order import Order
from app.models.order_file import OrderFile
from app.models.user import User
from app.workers.tasks import _run_pdf_task


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _make_file(**kw) -> OrderFile:
    """Minimal OrderFile stub returned by mock process_pdf_for_order."""
    defaults = dict(
        id=f"f-{kw.get('original_name', 'file')}",
        order_id="",
        order_version=1,
        original_name=kw.get("original_name", "test.pdf"),
        source_url=kw.get("source_url", "https://example.com/test.pdf"),
        parse_status=kw.get("parse_status", "READY"),
        error_code=kw.get("error_code"),
        error_message=kw.get("error_message"),
        storage_key=kw.get("storage_key"),
        sha256=kw.get("sha256"),
        content_type=kw.get("content_type", "application/pdf"),
        size_bytes=kw.get("size_bytes", 100),
        parsed_text=kw.get("parsed_text"),
    )
    return OrderFile(**defaults)


def _mock_process_pdf_for_order(monkeypatch, results: list[OrderFile]):
    """Replace process_pdf_for_order to return the given results in order.

    The mock is applied at the tasks module's import site.
    """
    call_count = [0]

    async def _mock(db, order_id, url, name, order_version):
        idx = call_count[0]
        call_count[0] += 1
        if idx < len(results):
            r = results[idx]
            # Override with actual call args
            r.order_id = order_id
            r.source_url = url
            r.original_name = name
            r.order_version = order_version
            return r
        # Default: READY
        return _make_file(original_name=name, source_url=url, parse_status="READY")

    monkeypatch.setattr("app.workers.tasks.process_pdf_for_order", _mock)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

async def _create_order_with_files(
    db_session,
    user_id: str,
    task_order_id: str,
    pipeline_status: str,
    files: list[dict],
) -> Order:
    """Create an order with existing OrderFile records in the DB."""
    user = User(arms_account=user_id, id=f"u-{user_id}")
    order = Order(
        id=f"ord-{task_order_id}",
        task_order_id=task_order_id,
        owner_user_id=user.id,
        pipeline_status=pipeline_status,
        order_version=1,
        detail_hash="test",
    )
    db_session.add_all([user, order])
    await db_session.flush()

    for f in files:
        of = OrderFile(
            order_id=order.id,
            order_version=1,
            original_name=f.get("name", "file.pdf"),
            source_url=f.get("url", "https://example.com/file.pdf"),
            parse_status=f.get("parse_status", "PENDING"),
        )
        db_session.add(of)
    await db_session.commit()
    return order


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunPdfTaskCounting:
    """Verify correct counting and final state transitions."""

    @pytest.mark.asyncio
    async def test_all_read_goes_to_pdf_ready(self, db_session, monkeypatch):
        """All files parse as READY → PDF_READY."""
        order = await _create_order_with_files(
            db_session, "all-ok", "TN-ALL-OK", "PDF_QUEUED",
            files=[{"name": "a.pdf"}, {"name": "b.pdf"}],
        )
        _mock_process_pdf_for_order(monkeypatch, [
            _make_file(original_name="a.pdf", parse_status="READY", parsed_text="text A"),
            _make_file(original_name="b.pdf", parse_status="READY", parsed_text="text B"),
        ])

        await _run_pdf_task(order.id, order.order_version)

        # Need to reload order from a fresh session since _run_pdf_task uses its own
        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.PDF_READY.value

    @pytest.mark.asyncio
    async def test_all_skipped_goes_to_manual_required(self, db_session, monkeypatch):
        """All files are non-PDF → MANUAL_REQUIRED."""
        order = await _create_order_with_files(
            db_session, "all-skip", "TN-ALL-SKIP", "PDF_QUEUED",
            files=[{"name": "img1.png"}, {"name": "img2.jpg"}],
        )
        _mock_process_pdf_for_order(monkeypatch, [
            _make_file(original_name="img1.png", parse_status="SKIPPED", error_code="NOT_PDF"),
            _make_file(original_name="img2.jpg", parse_status="SKIPPED", error_code="NOT_PDF"),
        ])

        await _run_pdf_task(order.id, order.order_version)

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.MANUAL_REQUIRED.value

    @pytest.mark.asyncio
    async def test_all_ocr_required_goes_to_manual_required(self, db_session, monkeypatch):
        """All files are scanned PDFs → MANUAL_REQUIRED."""
        order = await _create_order_with_files(
            db_session, "all-ocr", "TN-ALL-OCR", "PDF_QUEUED",
            files=[{"name": "scan1.pdf"}, {"name": "scan2.pdf"}],
        )
        _mock_process_pdf_for_order(monkeypatch, [
            _make_file(original_name="scan1.pdf", parse_status="OCR_REQUIRED", error_code="OCR_REQUIRED"),
            _make_file(original_name="scan2.pdf", parse_status="OCR_REQUIRED", error_code="OCR_REQUIRED"),
        ])

        await _run_pdf_task(order.id, order.order_version)

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.MANUAL_REQUIRED.value

    @pytest.mark.asyncio
    async def test_mixed_ready_and_skipped_goes_to_pdf_ready(self, db_session, monkeypatch):
        """Some READY + some SKIPPED → still PDF_READY."""
        order = await _create_order_with_files(
            db_session, "mixed-1", "TN-MIXED-1", "PDF_QUEUED",
            files=[
                {"name": "report.pdf"},
                {"name": "screenshot.png"},
                {"name": "cert.pdf"},
            ],
        )
        _mock_process_pdf_for_order(monkeypatch, [
            _make_file(original_name="report.pdf", parse_status="READY", parsed_text="OK"),
            _make_file(original_name="screenshot.png", parse_status="SKIPPED", error_code="NOT_PDF"),
            _make_file(original_name="cert.pdf", parse_status="READY", parsed_text="OK"),
        ])

        await _run_pdf_task(order.id, order.order_version)

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.PDF_READY.value

    @pytest.mark.asyncio
    async def test_mixed_ready_and_ocr_goes_to_pdf_ready(self, db_session, monkeypatch):
        """Some READY + some OCR_REQUIRED → still PDF_READY."""
        order = await _create_order_with_files(
            db_session, "mixed-2", "TN-MIXED-2", "PDF_QUEUED",
            files=[
                {"name": "ok.pdf"},
                {"name": "scanned.pdf"},
            ],
        )
        _mock_process_pdf_for_order(monkeypatch, [
            _make_file(original_name="ok.pdf", parse_status="READY", parsed_text="text"),
            _make_file(original_name="scanned.pdf", parse_status="OCR_REQUIRED", error_code="OCR_REQUIRED"),
        ])

        await _run_pdf_task(order.id, order.order_version)

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.PDF_READY.value

    @pytest.mark.asyncio
    async def test_any_failed_still_triggers_pdf_failed(self, db_session, monkeypatch):
        """Any true failure (not SKIPPED/OCR_REQUIRED) → PDF_FAILED."""
        order = await _create_order_with_files(
            db_session, "has-fail", "TN-HAS-FAIL", "PDF_QUEUED",
            files=[
                {"name": "ok.pdf"},
                {"name": "bad.pdf"},
            ],
        )
        _mock_process_pdf_for_order(monkeypatch, [
            _make_file(original_name="ok.pdf", parse_status="READY", parsed_text="text"),
            _make_file(original_name="bad.pdf", parse_status="FAILED", error_code="PDF_CORRUPTED"),
        ])

        await _run_pdf_task(order.id, order.order_version)

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.PDF_FAILED.value

    @pytest.mark.asyncio
    async def test_failed_dominates_over_skipped(self, db_session, monkeypatch):
        """FAILED + SKIPPED → PDF_FAILED (failed takes priority)."""
        order = await _create_order_with_files(
            db_session, "fail-dom", "TN-FAIL-DOM", "PDF_QUEUED",
            files=[
                {"name": "img.png"},
                {"name": "corrupt.pdf"},
            ],
        )
        _mock_process_pdf_for_order(monkeypatch, [
            _make_file(original_name="img.png", parse_status="SKIPPED", error_code="NOT_PDF"),
            _make_file(original_name="corrupt.pdf", parse_status="FAILED", error_code="PDF_CORRUPTED"),
        ])

        await _run_pdf_task(order.id, order.order_version)

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.PDF_FAILED.value

    @pytest.mark.asyncio
    async def test_skip_count_does_not_block(self, db_session, monkeypatch):
        """1 READY + many SKIPPED → PDF_READY (skip count doesn't matter)."""
        order = await _create_order_with_files(
            db_session, "many-skip", "TN-MANY-SKIP", "PDF_QUEUED",
            files=[
                {"name": "a.png"}, {"name": "b.jpg"}, {"name": "c.gif"}, {"name": "d.png"},
                {"name": "real.pdf"},
            ],
        )
        _mock_process_pdf_for_order(monkeypatch, [
            _make_file(original_name="a.png", parse_status="SKIPPED", error_code="NOT_PDF"),
            _make_file(original_name="b.jpg", parse_status="SKIPPED", error_code="NOT_PDF"),
            _make_file(original_name="c.gif", parse_status="SKIPPED", error_code="NOT_PDF"),
            _make_file(original_name="d.png", parse_status="SKIPPED", error_code="NOT_PDF"),
            _make_file(original_name="real.pdf", parse_status="READY", parsed_text="success"),
        ])

        await _run_pdf_task(order.id, order.order_version)

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.PDF_READY.value

    @pytest.mark.asyncio
    async def test_mixed_skip_ocr_goes_to_manual_required_when_no_ready(self, db_session, monkeypatch):
        """SKIPPED + OCR_REQUIRED, no READY → MANUAL_REQUIRED."""
        order = await _create_order_with_files(
            db_session, "mix-skip-ocr", "TN-MIX-SKIP-OCR", "PDF_QUEUED",
            files=[
                {"name": "img.png"},
                {"name": "scanned.pdf"},
            ],
        )
        _mock_process_pdf_for_order(monkeypatch, [
            _make_file(original_name="img.png", parse_status="SKIPPED", error_code="NOT_PDF"),
            _make_file(original_name="scanned.pdf", parse_status="OCR_REQUIRED", error_code="OCR_REQUIRED"),
        ])

        await _run_pdf_task(order.id, order.order_version)

        await db_session.refresh(order)
        assert order.pipeline_status == PipelineStatus.MANUAL_REQUIRED.value
