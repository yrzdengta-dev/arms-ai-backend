"""
Pipeline status state machine.

Valid transitions (forward-only):
  RECEIVED -> PDF_QUEUED
  PDF_QUEUED -> PDF_DOWNLOADING
  PDF_DOWNLOADING -> PDF_READY | PDF_FAILED
  PDF_READY -> ROUTING
  ROUTING -> AI_QUEUED | MANUAL_REQUIRED
  AI_QUEUED -> AI_RUNNING
  AI_RUNNING -> AI_COMPLETED | MANUAL_REQUIRED
  PDF_FAILED -> FAILED_RETRYABLE | FAILED_FINAL
  AI_COMPLETED -> (terminal)
  MANUAL_REQUIRED -> (terminal)
  FAILED_FINAL -> (terminal)
  FAILED_RETRYABLE -> PDF_QUEUED (retry)
"""

import logging
from enum import StrEnum

logger = logging.getLogger(__name__)


class PipelineStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PDF_QUEUED = "PDF_QUEUED"
    PDF_DOWNLOADING = "PDF_DOWNLOADING"
    PDF_READY = "PDF_READY"
    PDF_FAILED = "PDF_FAILED"
    ROUTING = "ROUTING"
    AI_QUEUED = "AI_QUEUED"
    AI_RUNNING = "AI_RUNNING"
    AI_COMPLETED = "AI_COMPLETED"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"


VALID_TRANSITIONS: dict[PipelineStatus, set[PipelineStatus]] = {
    PipelineStatus.RECEIVED: {PipelineStatus.PDF_QUEUED},
    PipelineStatus.PDF_QUEUED: {PipelineStatus.PDF_DOWNLOADING},
    PipelineStatus.PDF_DOWNLOADING: {PipelineStatus.PDF_READY, PipelineStatus.PDF_FAILED},
    PipelineStatus.PDF_READY: {PipelineStatus.ROUTING},
    PipelineStatus.ROUTING: {PipelineStatus.AI_QUEUED, PipelineStatus.MANUAL_REQUIRED},
    PipelineStatus.AI_QUEUED: {PipelineStatus.AI_RUNNING},
    PipelineStatus.AI_RUNNING: {PipelineStatus.AI_COMPLETED, PipelineStatus.MANUAL_REQUIRED},
    PipelineStatus.PDF_FAILED: {PipelineStatus.FAILED_RETRYABLE, PipelineStatus.FAILED_FINAL},
    PipelineStatus.FAILED_RETRYABLE: {PipelineStatus.PDF_QUEUED},
    PipelineStatus.AI_COMPLETED: set(),
    PipelineStatus.MANUAL_REQUIRED: set(),
    PipelineStatus.FAILED_FINAL: set(),
}

TERMINAL_STATUSES = {
    PipelineStatus.AI_COMPLETED,
    PipelineStatus.MANUAL_REQUIRED,
    PipelineStatus.FAILED_FINAL,
}


def can_transition(current: PipelineStatus, target: PipelineStatus) -> bool:
    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


def validate_transition(current: PipelineStatus, target: PipelineStatus, order_id: str) -> bool:
    if not can_transition(current, target):
        logger.warning(
            "Invalid state transition order_id=%s current=%s target=%s",
            order_id,
            current.value,
            target.value,
        )
        return False
    return True
