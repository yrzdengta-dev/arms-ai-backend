"""SSRF-safe URL validation for PDF download.

Blocks internal IPs, private networks, cloud metadata endpoints,
non-HTTPS schemes, credential-embedded URLs, and unsafe redirect targets.

Uses ipaddress module attributes (is_private / is_loopback / is_link_local /
is_reserved / is_unspecified / is_multicast) for comprehensive IP blocking
rather than hand-maintained CIDR lists.

validate_pdf_url() performs synchronous checks only (scheme, hostname, literal IPs).
DNS hostname resolution must be done via async resolve_hostname_public_ips().
"""

import asyncio
import ipaddress
import logging
import re
import socket
from typing import Any, cast
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class UnsafeURLException(Exception):  # noqa: N818
    """Raised when a URL is unsafe for download (SSRF protection)."""


# Known metadata hostnames to block by name (checked before DNS resolution)
_BLOCKED_METADATA_HOSTS: set[str] = {
    "metadata.google.internal",
    "metadata.google",
    "169.254.169.254",
}
type AddrInfo = tuple[int, int, int, str, tuple[Any, ...]]


def validate_pdf_url(url: str) -> str:
    """Validate a PDF URL is safe to download (sync checks only).

    Checks scheme, credentials, hostname, blocked names, literal IPs.
    Does NOT perform DNS resolution — call resolve_hostname_public_ips()
    from an async context for DNS-level validation.

    Returns the normalized URL if valid.
    Raises UnsafeURLException if the URL is unsafe.
    """
    if not url or not url.strip():
        raise UnsafeURLException("Blocked: URL is empty")

    url = url.strip()

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    # Only allow HTTPS
    if scheme != "https":
        raise UnsafeURLException(
            f"Blocked: only HTTPS allowed for PDF download, got scheme={scheme}"
        )

    # Block credentials in URL
    if parsed.username or parsed.password:
        raise UnsafeURLException(
            "Blocked: URL must not contain credentials (userinfo)"
        )

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLException("Blocked: URL has no valid hostname")

    hostname = hostname.lower().strip()

    # Block metadata hostnames by name
    if hostname in _BLOCKED_METADATA_HOSTS:
        raise UnsafeURLException(
            f"Blocked: hostname '{hostname}' is a metadata endpoint"
        )

    # Block localhost by name
    if hostname in ("localhost", "localhost.localdomain", "0.0.0.0"):
        raise UnsafeURLException(
            f"Blocked: hostname '{hostname}' is internal / localhost"
        )

    # Block .local TLD (mDNS)
    if hostname.endswith(".local"):
        raise UnsafeURLException(
            f"Blocked: hostname '{hostname}' uses restricted .local domain"
        )

    # Validate literal IP encodings (decimal, hex, dotted quad / IPv6)
    _check_literal_ip_safe(hostname)

    return url


def _check_literal_ip_safe(hostname: str) -> None:
    """Check if hostname is a literal IP (or encoded form) and validate it.

    Does NOT resolve DNS hostnames — those must be validated via
    resolve_hostname_public_ips() from an async context.
    """
    # Decimal integer: 2130706433 = 127.0.0.1
    if re.match(r"^\d{8,12}$", hostname):
        try:
            ip = ipaddress.ip_address(int(hostname))
        except ValueError:
            pass
        else:
            _verify_ip_safe(ip)
            return

    # Hex: 0x7f000001 = 127.0.0.1
    if re.match(r"^0[xX][0-9a-fA-F]{8}$", hostname):
        try:
            ip = ipaddress.ip_address(int(hostname, 16))
        except ValueError:
            pass
        else:
            _verify_ip_safe(ip)
            return

    # Try as literal IP (dotted quad, IPv6)
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return  # Not a literal IP — DNS resolution needed (call async function)

    _verify_ip_safe(ip)


async def resolve_hostname_public_ips(
    hostname: str, port: int = 443
) -> list[AddrInfo]:
    """Resolve a hostname via DNS and return only public IP addrinfo entries.

    Raises UnsafeURLException if:
      - DNS resolution fails
      - No addresses resolved
      - ANY resolved IP is non-public
      - ALL resolved IPs are non-public (after filtering)

    Returns list of (family, type, proto, canonname, sockaddr) suitable for
    passing to socket connection or monkey-patching getaddrinfo.
    """
    # First check: if it's a literal IP, just validate and return
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        _verify_ip_safe(ip)
        # Construct a synthetic addrinfo entry for an IP
        family = socket.AF_INET if ip.version == 4 else socket.AF_INET6
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (str(ip), port))]

    # DNS resolution
    loop = asyncio.get_running_loop()
    try:
        resolved_infos = await loop.getaddrinfo(
            hostname,
            port,
            proto=socket.IPPROTO_TCP,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        raise UnsafeURLException(
            f"Blocked: DNS resolution failed for hostname '{hostname}': {e}"
        ) from e

    if not resolved_infos:
        raise UnsafeURLException(
            f"Blocked: no addresses resolved for hostname '{hostname}'"
        )
    infos = [cast("AddrInfo", info) for info in resolved_infos]

    # Validate every resolved IP is public
    seen_ips: set[str] = set()
    for info in infos:
        addr = info[4][0]
        if addr in seen_ips:
            continue
        seen_ips.add(addr)
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as e:
            raise UnsafeURLException(
                f"Blocked: cannot parse resolved IP '{addr}' for hostname '{hostname}'"
            ) from e
        _verify_ip_safe(ip)

    # Filter to public IPs only (for DNS pinning)
    public_infos: list[AddrInfo] = []
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_global:
            public_infos.append(info)

    if not public_infos:
        raise UnsafeURLException(
            f"Blocked: all resolved IPs for '{hostname}' are non-public"
        )

    return public_infos


def _verify_ip_safe(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    """Raise UnsafeURLException if the IP is in any prohibited category.

    Uses Python stdlib ipaddress attributes rather than hand-maintained CIDR
    lists: is_private, is_loopback, is_link_local, is_reserved, is_unspecified,
    is_multicast, is_global.
    """
    reasons: list[str] = []

    if ip.is_loopback:
        reasons.append("loopback")
    if ip.is_private:
        reasons.append("private")
    if ip.is_link_local:
        reasons.append("link-local")
    if ip.is_reserved:
        reasons.append("reserved")
    if ip.is_unspecified:
        reasons.append("unspecified")
    if ip.is_multicast:
        reasons.append("multicast")

    if not ip.is_global:
        if not reasons:
            reasons.append("non-global")
        raise UnsafeURLException(
            f"Blocked: IP {ip} is not a public global address ({', '.join(reasons)})"
        )


def is_ip_safe(ip_str: str) -> bool:
    """Check if an IP string is safe (public). Returns True if safe."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return bool(ip.is_global)
    except ValueError:
        return False
