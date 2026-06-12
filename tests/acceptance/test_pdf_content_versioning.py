"""Acceptance tests: PDF content versioning (P1-2).

Verifies:
- URL canonicalization strips signature params but NOT version params
- Same content + different sig → same detail_hash
- Different version param → different detail_hash
- compute_detail_hash changes when pdf_files change
"""

from app.repositories.order_repository import _canonicalize_for_hash, compute_detail_hash


class TestUrlCanonicalization:
    """_canonicalize_for_hash must preserve meaningful query params."""

    def test_s3_signature_params_stripped(self):
        """AWS S3 pre-signed params are stripped from URL hash."""
        url = (
            "https://bucket.s3.amazonaws.com/doc.pdf"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Credential=AKID%2F20240101%2Fus-east-1"
            "&X-Amz-Date=20240101T000000Z"
            "&X-Amz-Expires=3600"
            "&X-Amz-SignedHeaders=host"
            "&X-Amz-Signature=abcdef123456"
        )
        result = _canonicalize_for_hash(url, key="url")
        # All sig params stripped → bare URL
        assert result == "https://bucket.s3.amazonaws.com/doc.pdf", (
            f"Expected bare URL, got: {result}"
        )

    def test_version_param_preserved(self):
        """Version query param is NOT a signature param — must be preserved."""
        url_v1 = "https://cdn.example.com/report.pdf?version=1"
        url_v2 = "https://cdn.example.com/report.pdf?version=2"
        h1 = _canonicalize_for_hash(url_v1, key="url")
        h2 = _canonicalize_for_hash(url_v2, key="url")
        assert h1 != h2, (
            f"version=1 and version=2 must produce different hashes, "
            f"got: {h1} == {h2}"
        )

    def test_mixed_version_and_sig_params(self):
        """Version param preserved, sig params stripped."""
        mixed = (
            "https://cdn.example.com/report.pdf"
            "?version=3"
            "&X-Amz-Expires=3600"
            "&Signature=xyz"
        )
        result = _canonicalize_for_hash(mixed, key="url")
        assert "version=3" in result, f"version=3 should be preserved, got: {result}"
        assert "X-Amz-Expires" not in result
        assert "Signature" not in result

    def test_sig_refresh_produces_same_hash(self):
        """Pure signature refresh → same canonicalized URL."""
        url_a = (
            "https://cdn.example.com/doc.pdf"
            "?X-Amz-Expires=3600&X-Amz-Signature=sigA"
        )
        url_b = (
            "https://cdn.example.com/doc.pdf"
            "?X-Amz-Expires=7200&X-Amz-Signature=sigB"
        )
        assert _canonicalize_for_hash(url_a, key="url") == _canonicalize_for_hash(url_b, key="url"), (
            "Signature refresh must produce same canonical URL"
        )

    def test_no_query_url_unchanged(self):
        """URL without query params is returned unchanged."""
        url = "https://example.com/doc.pdf"
        assert _canonicalize_for_hash(url, key="url") == url

    def test_non_url_string_unchanged(self):
        """Non-URL strings are passed through unchanged."""
        assert _canonicalize_for_hash("hello world", key="url") == "hello world"

    def test_azure_sas_params_stripped(self):
        """Azure SAS token params (sig, se, sv, sp, spr) are stripped."""
        azure = (
            "https://storage.blob.core.windows.net/container/doc.pdf"
            "?sv=2021-06-08&se=2024-01-01T00%3A00%3A00Z"
            "&sr=b&sp=r&sig=abc123def456"
            "&version=2"
        )
        result = _canonicalize_for_hash(azure, key="url")
        # version=2 should be preserved
        assert "version=2" in result
        # Azure SAS sig params should be stripped
        assert "sig=" not in result
        assert "se=" not in result
        assert "sv=" not in result
        assert "sp=" not in result

    def test_query_order_independent(self):
        """Query param order should not affect canonicalization."""
        url_a = "https://cdn.example.com/doc.pdf?version=1&foo=bar"
        url_b = "https://cdn.example.com/doc.pdf?foo=bar&version=1"
        assert _canonicalize_for_hash(url_a, key="url") == _canonicalize_for_hash(url_b, key="url")


class TestComputeDetailHash:
    """compute_detail_hash must reflect PDF file changes."""

    def test_different_pdf_urls_different_hash(self):
        snapshot = {"skc": "SKC-001"}
        detail = {}
        pdf_a = [{"name": "a.pdf", "url": "https://cdn.example.com/a.pdf?version=1"}]
        pdf_b = [{"name": "a.pdf", "url": "https://cdn.example.com/a.pdf?version=2"}]

        h1 = compute_detail_hash(snapshot, detail, pdf_a)
        h2 = compute_detail_hash(snapshot, detail, pdf_b)
        assert h1 != h2, f"Different version params must produce different hashes: {h1} == {h2}"

    def test_same_pdf_url_same_hash(self):
        """Identical input → identical hash."""
        snapshot = {"skc": "SKC-001"}
        detail = {"key": "val"}
        pdf = [{"name": "a.pdf", "url": "https://cdn.example.com/a.pdf?v=1&sig=refresh"}]

        h1 = compute_detail_hash(snapshot, detail, pdf)
        h2 = compute_detail_hash(snapshot, detail, pdf)
        assert h1 == h2

    def test_pdf_name_change_different_hash(self):
        """Different file names → different hash."""
        snapshot = {"skc": "SKC-001"}
        detail = {}
        pdf_a = [{"name": "old.pdf", "url": "https://cdn.example.com/doc.pdf"}]
        pdf_b = [{"name": "new.pdf", "url": "https://cdn.example.com/doc.pdf"}]

        h1 = compute_detail_hash(snapshot, detail, pdf_a)
        h2 = compute_detail_hash(snapshot, detail, pdf_b)
        assert h1 != h2

    def test_empty_pdf_list_same_hash(self):
        """Empty pdf_files list should produce consistent hash."""
        snapshot = {"skc": "SKC-001"}
        h1 = compute_detail_hash(snapshot, {}, [])
        h2 = compute_detail_hash(snapshot, {}, None)
        assert h1 == h2
