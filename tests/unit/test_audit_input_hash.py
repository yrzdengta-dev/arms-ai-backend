"""Unit tests: audit_input_hash (P1-3).

Verifies hash includes all required dimensions and changes when any single
dimension changes.
"""

import hashlib
import json

from app.services.audit_service import compute_audit_input_hash


class TestAuditInputHashDimensions:
    """Hash must include all specified dimensions (AC6)."""

    BASE = {
        "prompt": "You are an auditor...",
        "order_snapshot": {"skc": "SKC-001", "product_name": "Widget"},
        "pdf_text": "Certificate for SKC-001...",
        "pdf_sha256s": ["abc123", "def456"],
        "skill_id": "simple_text_consistency",
        "skill_version": "1.0.0",
        "prompt_hash": "prompt-hash-12",
        "rules_hash": "rules-hash-34",
        "model_provider": "openai_compatible",
        "model_name": "gpt-4o",
        "protocol_version": 1,
    }

    def _hash(self, **overrides):
        args = {**self.BASE, **overrides}
        return compute_audit_input_hash(**args)

    def test_hash_stable_for_same_input(self):
        """Identical input → identical hash."""
        h1 = self._hash()
        h2 = self._hash()
        assert h1 == h2

    def test_prompt_change(self):
        assert self._hash(prompt="Different prompt") != self._hash()

    def test_order_snapshot_change(self):
        assert self._hash(order_snapshot={"skc": "SKC-999"}) != self._hash()

    def test_pdf_sha256s_change(self):
        assert self._hash(pdf_sha256s=["xyz789"]) != self._hash()

    def test_skill_id_change(self):
        assert self._hash(skill_id="other_skill") != self._hash()

    def test_skill_version_change(self):
        assert self._hash(skill_version="2.0.0") != self._hash()

    def test_prompt_hash_change(self):
        assert self._hash(prompt_hash="different-prompt-hash") != self._hash()

    def test_rules_hash_change(self):
        assert self._hash(rules_hash="different-rules-hash") != self._hash()

    def test_model_provider_change(self):
        assert self._hash(model_provider="aws_bedrock") != self._hash()

    def test_model_name_change(self):
        assert self._hash(model_name="claude-opus-4-8") != self._hash()

    def test_protocol_version_change(self):
        assert self._hash(protocol_version=2) != self._hash()

    def test_hash_order_independent_for_pdf_sha256s(self):
        """PDF SHA-256 order must not affect hash."""
        h1 = compute_audit_input_hash(
            prompt="p", order_snapshot={}, pdf_text="t",
            pdf_sha256s=["aaa", "bbb", "ccc"],
            skill_id="s", skill_version="1", prompt_hash="ph",
            rules_hash="rh", model_provider="mp", model_name="mn",
            protocol_version=1,
        )
        h2 = compute_audit_input_hash(
            prompt="p", order_snapshot={}, pdf_text="t",
            pdf_sha256s=["ccc", "aaa", "bbb"],
            skill_id="s", skill_version="1", prompt_hash="ph",
            rules_hash="rh", model_provider="mp", model_name="mn",
            protocol_version=1,
        )
        assert h1 == h2

    def test_hash_not_depend_on_timestamps(self):
        """No timestamp or non-deterministic values in hash."""
        h1 = self._hash()
        # Wait 0.1s — hash should be identical
        import time
        time.sleep(0.1)
        h2 = self._hash()
        assert h1 == h2


class TestAuditInputHashBackwardCompat:
    """New hash must not contain pdf_text_len only (old bug)."""

    def test_hash_contains_sha256s_not_text_len(self):
        h = compute_audit_input_hash(
            prompt="p",
            order_snapshot={},
            pdf_text="t",
            pdf_sha256s=["aaa111", "bbb222"],
            skill_id="s", skill_version="1",
            prompt_hash="ph", rules_hash="rh",
            model_provider="mp", model_name="mn",
            protocol_version=1,
        )
        # PDF SHA-256s must be IN the hash input
        # (not just pdf_text_len as before)
        assert h != compute_audit_input_hash(
            prompt="p",
            order_snapshot={},
            pdf_text="t",
            pdf_sha256s=[],
            skill_id="s", skill_version="1",
            prompt_hash="ph", rules_hash="rh",
            model_provider="mp", model_name="mn",
            protocol_version=1,
        )
