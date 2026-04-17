"""URL validation + safe fetching with DNS-rebinding-resistant transport.

``validate_url`` returns a :class:`~app.core.ssrf.URLValidation` so callers
can forward ``(hostname, pinned_ip)`` into a :class:`PinnedDNSTransport`
and keep the pin across the redirect chain. Existing callers that discard
the return value continue to work — validation itself still raises on
unsafe URLs.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.core.exceptions import ValidationError
from app.core.ssrf import (
    PinnedDNSTransport,
    URLValidation,
    is_blocked_host,
    is_blocked_ip,
    resolve_and_pin,
)

MAX_REDIRECTS = 5


async def validate_url(url: str) -> URLValidation:
    """Validate scheme + host; resolve DNS; return pinned IP for the caller."""
    parsed = urlparse(url)
    allowed_schemes = set(settings.ALLOWED_URL_SCHEMES)
    if parsed.scheme not in allowed_schemes:
        raise ValidationError("INVALID_URL", "URL scheme must be http or https")

    hostname = parsed.hostname
    if not hostname:
        raise ValidationError("INVALID_URL", "URL has no hostname")

    if is_blocked_host(hostname):
        raise ValidationError("BLOCKED_URL", "This URL target is not allowed")

    # Literal-IP hostnames skip DNS but still go through the IP blocklist.
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        pinned = await resolve_and_pin(hostname)
    else:
        if is_blocked_ip(literal):
            raise ValidationError(
                "BLOCKED_URL", "URL resolves to a private/internal IP address"
            )
        pinned = str(literal)

    return URLValidation(url=url, hostname=hostname, pinned_ip=pinned)


async def fetch_url_content(url: str, max_size: int = 50 * 1024 * 1024) -> bytes:
    """Download URL content with manual redirect loop, per-hop validation,
    per-hop DNS pinning, and a streaming byte-count cap.
    """
    current_url = url
    for _ in range(MAX_REDIRECTS + 1):
        validation = await validate_url(current_url)
        transport = PinnedDNSTransport(
            pinned_ip=validation.pinned_ip,
            original_host=validation.hostname,
        )
        try:
            async with httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                timeout=httpx.Timeout(30.0, connect=10.0),
            ) as client, client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValidationError(
                            "INVALID_URL",
                            "Redirect response missing Location header",
                        )
                    # Resolve relative redirects against the current URL.
                    current_url = str(httpx.URL(current_url).join(location))
                    continue

                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes(8192):
                    total += len(chunk)
                    if total > max_size:
                        raise ValidationError(
                            "CONTENT_TOO_LARGE",
                            f"Content exceeds {max_size // (1024 * 1024)}MB limit",
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
        finally:
            # ``AsyncClient`` + ``stream`` context close on exit; transport's
            # pool is owned by the client and gets disposed with it.
            pass

    raise ValidationError("TOO_MANY_REDIRECTS", "Too many redirects while fetching URL")
