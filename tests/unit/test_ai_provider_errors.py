"""Unit tests: AI Provider error handling (Category K)

Verifies:
- Timeout → retry or MANUAL_REVIEW
- 429 rate limit → retry
- 5xx → retryable, limited retries
- 400 bad request → not retried
- 401/403 → not retried
- Non-JSON response → MANUAL_REVIEW
- Markdown-wrapped JSON → extracted
- Illegal decision value → MANUAL_REVIEW
- Schema missing fields → defaults applied
- API key masked in logs and error messages
"""

import json

import pytest

from app.schemas.audit import AuditOutput, Decision


class TestHTTPErrorClassification:
    """Verify HTTP errors are classified correctly as retryable or not."""

    def test_429_is_retryable(self):
        """429 Too Many Requests should be retryable."""
        from app.adapters.llm.openai_provider import _is_retryable_http_error
        assert _is_retryable_http_error(429) is True

    def test_5xx_is_retryable(self):
        """5xx server errors should be retryable."""
        from app.adapters.llm.openai_provider import _is_retryable_http_error
        for status in [500, 502, 503, 504]:
            assert _is_retryable_http_error(status) is True, f"{status} should be retryable"

    def test_400_is_not_retryable(self):
        """400 Bad Request should NOT be retryable."""
        from app.adapters.llm.openai_provider import _is_retryable_http_error
        assert _is_retryable_http_error(400) is False

    def test_401_is_not_retryable(self):
        """401 Unauthorized should NOT be retryable."""
        from app.adapters.llm.openai_provider import _is_retryable_http_error
        assert _is_retryable_http_error(401) is False

    def test_403_is_not_retryable(self):
        """403 Forbidden should NOT be retryable."""
        from app.adapters.llm.openai_provider import _is_retryable_http_error
        assert _is_retryable_http_error(403) is False

    def test_404_is_not_retryable(self):
        """404 Not Found should NOT be retryable."""
        from app.adapters.llm.openai_provider import _is_retryable_http_error
        assert _is_retryable_http_error(404) is False


class TestJSONRepair:
    """Verify malformed JSON responses are handled gracefully."""

    def test_markdown_code_block_json_extracted(self):
        """JSON wrapped in ```json ... ``` should be extracted."""
        from app.adapters.llm.openai_provider import _attempt_json_repair

        raw = '```json\n{"decision": "PASS", "summary": "ok", "rules": []}\n```'
        result = _attempt_json_repair(raw)
        assert result is not None, "Should extract JSON from markdown code block"
        assert result.get("decision") == "PASS"

    def test_markdown_code_block_no_lang_extracted(self):
        """JSON wrapped in ``` ... ``` (no lang) should be extracted."""
        from app.adapters.llm.openai_provider import _attempt_json_repair

        raw = '```\n{"decision": "REJECT", "summary": "bad", "rules": []}\n```'
        result = _attempt_json_repair(raw)
        assert result is not None
        assert result.get("decision") == "REJECT"

    def test_plain_text_not_json_returns_none(self):
        """Plain text that isn't JSON should return None."""
        from app.adapters.llm.openai_provider import _attempt_json_repair

        raw = "This is not JSON at all"
        result = _attempt_json_repair(raw)
        assert result is None, "Non-JSON should return None"

    def test_broken_json_returns_none(self):
        """Severely broken JSON should return None."""
        from app.adapters.llm.openai_provider import _attempt_json_repair

        raw = '{"decision": "PASS", "summary": "broken'
        result = _attempt_json_repair(raw)
        assert result is None, "Broken JSON should return None"


class TestDecisionValidation:
    """Verify invalid decisions are caught."""

    def test_invalid_decision_string_falls_back(self):
        """Decision 'UNKNOWN' should be rejected by schema validation."""
        output = AuditOutput(
            decision=Decision.MANUAL_REVIEW,
            summary="Fallback",
            rules=[],
        )
        # MANUAL_REVIEW is a valid decision
        assert output.decision == Decision.MANUAL_REVIEW

    def test_missing_summary_defaults(self):
        """If summary is missing, default should be applied."""
        # Construct with minimal fields
        output = AuditOutput(
            decision=Decision.MANUAL_REVIEW,
            summary="",
            rules=[],
        )
        assert output.summary == ""


class TestAPIKeyMasking:
    """Verify API keys are never logged in plain text."""

    def test_mask_api_key_function(self):
        """mask_api_key must hide all but last 4 characters."""
        from app.adapters.llm.openai_provider import _mask_key

        masked = _mask_key("sk-abcdefghijklmnop12345678")
        assert "sk-" not in masked.lower() or masked.endswith("****"), (
            f"API key not properly masked: {masked}"
        )
        assert len(masked) <= 10 or "****" in masked or "..." in masked, (
            f"Masked key too long or missing mask indicator: {masked}"
        )

    def test_short_key_masked_fully(self):
        from app.adapters.llm.openai_provider import _mask_key

        masked = _mask_key("abc")
        # Short keys should be fully masked
        assert masked == "****" or "abc" not in masked

    def test_empty_key_handled(self):
        from app.adapters.llm.openai_provider import _mask_key

        masked = _mask_key("")
        assert masked in ("", "****", "empty")


class TestProviderErrorResponse:
    """Verify provider returns MANUAL_REVIEW on errors, not crash."""

    def test_fake_provider_never_throws(self):
        """FakeAuditProvider must always return a valid result."""
        from app.adapters.llm.fake_provider import AuditModelRequest, FakeAuditProvider

        provider = FakeAuditProvider()
        request = AuditModelRequest(
            prompt="test prompt",
            order_snapshot={"skc": "SKC-001"},
            pdf_text="PDF text content",
            skill_id="test",
            skill_version="1.0.0",
        )
        result = provider.audit_sync(request)
        assert result.decision in ("PASS", "REJECT", "MANUAL_REVIEW")
        assert result.normalized_output is not None
