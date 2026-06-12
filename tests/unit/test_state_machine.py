"""Unit tests: State machine transitions (tests 26-29)"""

from app.core.state_machine import PipelineStatus, can_transition, validate_transition


def test_valid_forward_transitions():
    assert can_transition(PipelineStatus.RECEIVED, PipelineStatus.PDF_QUEUED)
    assert can_transition(PipelineStatus.PDF_QUEUED, PipelineStatus.PDF_DOWNLOADING)
    assert can_transition(PipelineStatus.PDF_DOWNLOADING, PipelineStatus.PDF_READY)
    assert can_transition(PipelineStatus.PDF_DOWNLOADING, PipelineStatus.PDF_FAILED)
    assert can_transition(PipelineStatus.PDF_DOWNLOADING, PipelineStatus.MANUAL_REQUIRED)
    assert can_transition(PipelineStatus.PDF_READY, PipelineStatus.ROUTING)
    assert can_transition(PipelineStatus.ROUTING, PipelineStatus.AI_QUEUED)
    assert can_transition(PipelineStatus.ROUTING, PipelineStatus.MANUAL_REQUIRED)
    assert can_transition(PipelineStatus.AI_QUEUED, PipelineStatus.AI_RUNNING)
    assert can_transition(PipelineStatus.AI_RUNNING, PipelineStatus.AI_COMPLETED)
    assert can_transition(PipelineStatus.AI_RUNNING, PipelineStatus.MANUAL_REQUIRED)
    assert can_transition(PipelineStatus.PDF_FAILED, PipelineStatus.FAILED_RETRYABLE)
    assert can_transition(PipelineStatus.PDF_FAILED, PipelineStatus.FAILED_FINAL)
    assert can_transition(PipelineStatus.FAILED_RETRYABLE, PipelineStatus.PDF_QUEUED)


def test_invalid_transitions_rejected():
    assert not can_transition(PipelineStatus.RECEIVED, PipelineStatus.AI_COMPLETED)
    assert not can_transition(PipelineStatus.AI_COMPLETED, PipelineStatus.RECEIVED)
    assert not can_transition(PipelineStatus.MANUAL_REQUIRED, PipelineStatus.AI_RUNNING)
    assert not can_transition(PipelineStatus.FAILED_FINAL, PipelineStatus.PDF_QUEUED)
    assert not can_transition(PipelineStatus.AI_COMPLETED, PipelineStatus.AI_COMPLETED)


def test_terminal_states_have_no_exits():
    for terminal in [PipelineStatus.AI_COMPLETED, PipelineStatus.MANUAL_REQUIRED, PipelineStatus.FAILED_FINAL]:
        for target in PipelineStatus:
            assert not can_transition(terminal, target), f"{terminal} should not transition to {target}"


def test_validate_transition_returns_false_for_invalid():
    assert not validate_transition(
        PipelineStatus.AI_COMPLETED, PipelineStatus.RECEIVED, "test-order-id"
    )


def test_retry_transition_from_failed_retryable():
    """Retry must be allowed from FAILED_RETRYABLE back to RECEIVED."""
    assert can_transition(PipelineStatus.FAILED_RETRYABLE, PipelineStatus.RECEIVED), (
        "FAILED_RETRYABLE must be able to transition to RECEIVED for retry"
    )


def test_retry_transition_from_pdf_failed():
    """Retry must be allowed from PDF_FAILED back to RECEIVED."""
    assert can_transition(PipelineStatus.PDF_FAILED, PipelineStatus.RECEIVED), (
        "PDF_FAILED must be able to transition to RECEIVED for retry"
    )


def test_retry_not_allowed_from_terminal():
    """Retry must NOT be allowed from terminal states."""
    assert not can_transition(PipelineStatus.AI_COMPLETED, PipelineStatus.RECEIVED)
    assert not can_transition(PipelineStatus.FAILED_FINAL, PipelineStatus.RECEIVED)
    assert not can_transition(PipelineStatus.MANUAL_REQUIRED, PipelineStatus.RECEIVED)
