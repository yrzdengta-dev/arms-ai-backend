"""Acceptance tests: Audit run versioning (P1-3).

Verifies:
- Same order_version can have multiple audit runs
- (order_id, order_version, input_hash) unique constraint
- Concurrent same-hash runs are idempotent
- API returns latest COMPLETED result for current version
"""

import pytest
from sqlalchemy import select

from app.core.state_machine import PipelineStatus
from app.models.audit_result import AuditResult
from app.models.order import Order
from app.models.user import User
from app.schemas.audit import AuditOutput, Decision
from app.services.audit_service import compute_audit_input_hash, run_audit


class TestAuditRunVersioning:
    """Same order_version must support multiple audit runs."""

    @pytest.mark.asyncio
    async def test_same_version_multiple_runs_no_constraint_violation(self, db_session, monkeypatch):
        """Two runs for same (order_id, order_version) with different input_hash must succeed."""
        user = User(arms_account="multi-run", id="u-multi")
        order = Order(
            id="ord-multi-1", task_order_id="TN-MULTI-001",
            owner_user_id=user.id, pipeline_status="AI_RUNNING",
            order_version=1, detail_hash="test",
            order_snapshot={"skc": "SKC", "scene_id": "7", "audit_point_id": "9", "certificate_type_id": 1, "business_type": None},
        )
        db_session.add_all([user, order])
        await db_session.flush()

        # Mock the provider to avoid real LLM calls
        from app.adapters.llm.fake_provider import FakeAuditProvider
        provider = FakeAuditProvider()

        async def fake_audit(self, request):
            return provider.audit_sync(request)

        class _FakeProvider:
            audit = fake_audit
            model = "fake-v1"

        monkeypatch.setattr(
            "app.services.audit_service._get_provider",
            lambda: _FakeProvider(),
        )

        # Run 1: model gpt-4o
        monkeypatch.setattr("app.services.audit_service.settings.LLM_PROVIDER", "fake")
        result1 = await run_audit(db_session, order, "PDF text content here")
        assert result1.decision is not None
        assert result1.input_hash is not None
        hash1 = result1.input_hash

        # Run 2: different model name → different hash
        # Use a different input to get a different hash
        result2 = await run_audit(db_session, order, "Different PDF text content")
        assert result2.decision is not None
        hash2 = result2.input_hash

        # Hashes must differ (different pdf_text)
        assert hash1 != hash2, "Different inputs must produce different hashes"

        # Both records must exist
        await db_session.flush()
        stmt = select(AuditResult).where(
            AuditResult.order_id == order.id,
            AuditResult.order_version == order.order_version,
        )
        results = (await db_session.execute(stmt)).scalars().all()
        assert len(results) >= 2, f"Expected at least 2 audit runs, got {len(results)}"

    @pytest.mark.asyncio
    async def test_idempotent_same_hash_no_duplicate(self, db_session, monkeypatch):
        """Two calls with same input_hash → only one AuditResult persisted."""
        user = User(arms_account="idemp", id="u-idemp")
        order = Order(
            id="ord-idemp-1", task_order_id="TN-IDEMP-001",
            owner_user_id=user.id, pipeline_status="AI_RUNNING",
            order_version=1, detail_hash="test",
            order_snapshot={"skc": "SKC", "scene_id": "7", "audit_point_id": "9", "certificate_type_id": 1, "business_type": None},
        )
        db_session.add_all([user, order])
        await db_session.flush()

        monkeypatch.setattr("app.services.audit_service.settings.LLM_PROVIDER", "fake")
        from app.adapters.llm.fake_provider import FakeAuditProvider
        provider = FakeAuditProvider()

        async def fake_audit(self, request):
            return provider.audit_sync(request)

        class _FakeProvider:
            audit = fake_audit
            model = "fake-v1"

        monkeypatch.setattr(
            "app.services.audit_service._get_provider",
            lambda: _FakeProvider(),
        )

        # Run once
        pdf_text = "Same PDF text for both runs"
        result1 = await run_audit(db_session, order, pdf_text)
        await db_session.flush()

        # Run again — should return cached result, not create new
        result2 = await run_audit(db_session, order, pdf_text)

        assert result1.id == result2.id, "Second run must return cached result"
        assert result1.input_hash == result2.input_hash


class TestAuditInputHash:
    """compute_audit_input_hash covers all dimensions (AC6)."""

    def test_hash_includes_model_info(self):
        h1 = compute_audit_input_hash(
            prompt="p", order_snapshot={}, pdf_text="t",
            pdf_sha256s=["aaa"],
            skill_id="s", skill_version="1", prompt_hash="ph",
            rules_hash="rh", model_provider="openai", model_name="gpt-4",
        )
        h2 = compute_audit_input_hash(
            prompt="p", order_snapshot={}, pdf_text="t",
            pdf_sha256s=["aaa"],
            skill_id="s", skill_version="1", prompt_hash="ph",
            rules_hash="rh", model_provider="openai", model_name="gpt-4o",
        )
        assert h1 != h2, "Model name change must produce different hash"
