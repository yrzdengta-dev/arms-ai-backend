"""Acceptance tests: State machine end-to-end (Section 3.3)

Verifies the complete allowed path:
  RECEIVED → PDF_QUEUED → PDF_DOWNLOADING → PDF_READY → ROUTING
  → AI_QUEUED → AI_RUNNING → AI_COMPLETED

And failure paths:
  - No PDF → MANUAL_REQUIRED
  - Scanned PDF → MANUAL_REQUIRED
  - No skill match → MANUAL_REQUIRED
  - Final retry exhaustion → FAILED_FINAL

Worker must NOT skip PDF_QUEUED or AI_QUEUED states.
"""


from app.core.state_machine import PipelineStatus, can_transition


class TestValidFullPath:
    """Every step in the happy path must be a valid transition."""

    def test_received_to_pdf_queued(self):
        assert can_transition(PipelineStatus.RECEIVED, PipelineStatus.PDF_QUEUED)

    def test_pdf_queued_to_pdf_downloading(self):
        assert can_transition(PipelineStatus.PDF_QUEUED, PipelineStatus.PDF_DOWNLOADING)

    def test_pdf_downloading_to_pdf_ready(self):
        assert can_transition(PipelineStatus.PDF_DOWNLOADING, PipelineStatus.PDF_READY)

    def test_pdf_ready_to_routing(self):
        assert can_transition(PipelineStatus.PDF_READY, PipelineStatus.ROUTING)

    def test_routing_to_ai_queued(self):
        assert can_transition(PipelineStatus.ROUTING, PipelineStatus.AI_QUEUED)

    def test_ai_queued_to_ai_running(self):
        assert can_transition(PipelineStatus.AI_QUEUED, PipelineStatus.AI_RUNNING)

    def test_ai_running_to_ai_completed(self):
        assert can_transition(PipelineStatus.AI_RUNNING, PipelineStatus.AI_COMPLETED)

    def test_full_happy_path(self):
        """Walk the complete happy path end-to-end."""
        path = [
            (PipelineStatus.RECEIVED, PipelineStatus.PDF_QUEUED),
            (PipelineStatus.PDF_QUEUED, PipelineStatus.PDF_DOWNLOADING),
            (PipelineStatus.PDF_DOWNLOADING, PipelineStatus.PDF_READY),
            (PipelineStatus.PDF_READY, PipelineStatus.ROUTING),
            (PipelineStatus.ROUTING, PipelineStatus.AI_QUEUED),
            (PipelineStatus.AI_QUEUED, PipelineStatus.AI_RUNNING),
            (PipelineStatus.AI_RUNNING, PipelineStatus.AI_COMPLETED),
        ]
        for current, target in path:
            assert can_transition(current, target), (
                f"Transition {current.value} → {target.value} must be valid"
            )


class TestFailurePaths:
    """Verify correct failure path transitions."""

    def test_pdf_downloading_to_pdf_failed(self):
        assert can_transition(PipelineStatus.PDF_DOWNLOADING, PipelineStatus.PDF_FAILED)

    def test_pdf_failed_to_failed_retryable(self):
        assert can_transition(PipelineStatus.PDF_FAILED, PipelineStatus.FAILED_RETRYABLE)

    def test_pdf_failed_to_failed_final(self):
        assert can_transition(PipelineStatus.PDF_FAILED, PipelineStatus.FAILED_FINAL)

    def test_failed_retryable_to_pdf_queued(self):
        """Retry goes back to PDF_QUEUED."""
        assert can_transition(PipelineStatus.FAILED_RETRYABLE, PipelineStatus.PDF_QUEUED)

    def test_routing_to_manual_required(self):
        assert can_transition(PipelineStatus.ROUTING, PipelineStatus.MANUAL_REQUIRED)

    def test_ai_running_to_manual_required(self):
        assert can_transition(PipelineStatus.AI_RUNNING, PipelineStatus.MANUAL_REQUIRED)


class TestSkippingStatesRejected:
    """Worker code must NOT skip states."""

    def test_received_to_pdf_downloading_is_invalid(self):
        """RECEIVED → PDF_DOWNLOADING skips PDF_QUEUED — must be invalid."""
        assert not can_transition(PipelineStatus.RECEIVED, PipelineStatus.PDF_DOWNLOADING), (
            "RECEIVED → PDF_DOWNLOADING skips PDF_QUEUED"
        )

    def test_received_to_pdf_ready_is_invalid(self):
        """Must not jump directly to PDF_READY."""
        assert not can_transition(PipelineStatus.RECEIVED, PipelineStatus.PDF_READY)

    def test_routing_to_ai_running_is_invalid(self):
        """ROUTING → AI_RUNNING skips AI_QUEUED — must be invalid."""
        assert not can_transition(PipelineStatus.ROUTING, PipelineStatus.AI_RUNNING), (
            "ROUTING → AI_RUNNING skips AI_QUEUED"
        )

    def test_pdf_ready_to_ai_running_is_invalid(self):
        """Must not skip ROUTING and AI_QUEUED."""
        assert not can_transition(PipelineStatus.PDF_READY, PipelineStatus.AI_RUNNING)


class TestTerminalStates:
    """Terminal states must have no valid exits."""

    def test_ai_completed_is_terminal(self):
        assert can_transition(PipelineStatus.AI_COMPLETED, PipelineStatus.AI_COMPLETED) is False  # no self-loop either
        for s in PipelineStatus:
            assert not can_transition(PipelineStatus.AI_COMPLETED, s), (
                f"AI_COMPLETED must have no exit, but {s.value} is reachable"
            )

    def test_manual_required_is_terminal(self):
        for s in PipelineStatus:
            assert not can_transition(PipelineStatus.MANUAL_REQUIRED, s), (
                f"MANUAL_REQUIRED must have no exit, but {s.value} is reachable"
            )

    def test_failed_final_is_terminal(self):
        for s in PipelineStatus:
            assert not can_transition(PipelineStatus.FAILED_FINAL, s), (
                f"FAILED_FINAL must have no exit, but {s.value} is reachable"
            )
