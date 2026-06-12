"""Security tests: SSRF protection for PDF downloader (Category E)

Verifies URL validation blocks internal/private/restricted targets.
"""

import pytest

from app.adapters.pdf.url_validator import UnsafeURLException, validate_pdf_url


class TestProtocolValidation:
    """Only HTTPS URLs should be allowed for PDF downloads."""

    def test_https_allowed(self):
        validate_pdf_url("https://example.com/document.pdf")

    def test_http_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)https|scheme|blocked"):
            validate_pdf_url("http://example.com/document.pdf")

    def test_file_protocol_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)scheme|blocked"):
            validate_pdf_url("file:///etc/passwd")

    def test_ftp_protocol_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)scheme|blocked"):
            validate_pdf_url("ftp://evil.com/document.pdf")

    def test_empty_string_blocked(self):
        with pytest.raises(UnsafeURLException):
            validate_pdf_url("")


class TestLocalhostBlocking:
    """Localhost and loopback addresses must be blocked."""

    def test_localhost_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)internal|localhost|blocked"):
            validate_pdf_url("https://localhost:8080/document.pdf")

    def test_localhost_with_subdomain_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)internal|localhost|blocked"):
            validate_pdf_url("https://localhost/document.pdf")

    def test_127_0_0_1_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)internal|private|blocked"):
            validate_pdf_url("https://127.0.0.1/document.pdf")

    def test_127_0_0_1_variants_blocked(self):
        for ip in ["127.0.0.2", "127.1.2.3", "127.255.255.255"]:
            with pytest.raises(UnsafeURLException, match="(?i)internal|private|blocked"):
                validate_pdf_url(f"https://{ip}/document.pdf")

    def test_ipv6_loopback_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)internal|private|blocked"):
            validate_pdf_url("https://[::1]/document.pdf")


class TestPrivateIPBlocking:
    """RFC1918 and other private/restricted IPs must be blocked."""

    def test_10_x_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)private|blocked"):
            validate_pdf_url("https://10.0.0.1/document.pdf")

    def test_172_16_x_blocked(self):
        for ip in ["172.16.0.1", "172.20.0.1", "172.31.255.254"]:
            with pytest.raises(UnsafeURLException, match="(?i)private|blocked"):
                validate_pdf_url(f"https://{ip}/document.pdf")

    def test_192_168_x_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)private|blocked"):
            validate_pdf_url("https://192.168.1.1/document.pdf")

    def test_link_local_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)private|blocked"):
            validate_pdf_url("https://169.254.169.254/document.pdf")


class TestCloudMetadataBlocking:
    """Cloud metadata endpoints must be blocked."""

    def test_aws_metadata_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)blocked"):
            validate_pdf_url("https://169.254.169.254/latest/meta-data/")

    def test_gcp_metadata_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)blocked|metadata"):
            validate_pdf_url("https://metadata.google.internal/computeMetadata/v1/")

    def test_azure_metadata_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)blocked"):
            validate_pdf_url("https://169.254.169.254/metadata/instance")


class TestCredentialInURL:
    """URLs with embedded credentials must be rejected."""

    def test_user_password_in_url_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)credential|userinfo|blocked"):
            validate_pdf_url("https://admin:secret@example.com/document.pdf")

    def test_user_only_in_url_blocked(self):
        with pytest.raises(UnsafeURLException, match="(?i)credential|userinfo|blocked"):
            validate_pdf_url("https://admin@example.com/document.pdf")


class TestURLFormatValidation:
    """URL format and hostname validation."""

    def test_no_hostname_blocked(self):
        with pytest.raises(UnsafeURLException):
            validate_pdf_url("https:///path/only")

    def test_valid_public_url_passes(self):
        validate_pdf_url("https://cdn.example.com/reports/2024/cert.pdf")
        validate_pdf_url("https://storage.googleapis.com/bucket/doc.pdf")
        validate_pdf_url("https://s3.amazonaws.com/bucket/doc.pdf")


class TestIPAddressResolution:
    """Verify IP address parsing catches all variants."""

    def test_decimal_ip_blocked(self):
        with pytest.raises(UnsafeURLException):
            validate_pdf_url("https://2130706433/document.pdf")

    def test_hex_ip_blocked(self):
        with pytest.raises(UnsafeURLException):
            validate_pdf_url("https://0x7f000001/document.pdf")


class TestSSRFInDownloader:
    """Verify the actual downloader validates URLs before fetching."""

    @pytest.mark.asyncio
    async def test_downloader_blocks_localhost_before_http_request(self):
        from app.adapters.pdf.downloader import PdfDownloadError, download_pdf
        try:
            await download_pdf("https://localhost:9999/nonexistent.pdf")
            pytest.fail("Should have raised for localhost URL")
        except PdfDownloadError:
            pytest.fail(
                "Got PdfDownloadError for localhost — "
                "SSRF check must happen BEFORE HTTP request"
            )
        except UnsafeURLException:
            pass
