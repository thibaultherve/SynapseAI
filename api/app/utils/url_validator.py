import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.core.exceptions import ValidationError

BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "[::1]",
    "metadata.google.internal",
    "metadata.internal",
}


async def validate_url(url: str) -> str:
    """Validate URL: scheme whitelist, async DNS resolve, block private/loopback IPs."""
    parsed = urlparse(url)
    allowed_schemes = set(settings.ALLOWED_URL_SCHEMES)
    if parsed.scheme not in allowed_schemes:
        raise ValidationError("INVALID_URL", "URL scheme must be http or https")

    hostname = parsed.hostname
    if not hostname:
        raise ValidationError("INVALID_URL", "URL has no hostname")
    if hostname.lower() in BLOCKED_HOSTS:
        raise ValidationError("BLOCKED_URL", "This URL target is not allowed")

    loop = asyncio.get_running_loop()
    try:
        resolved = await loop.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValidationError("UNRESOLVABLE_URL", f"Cannot resolve hostname: {hostname}") from exc

    for _, _, _, _, sockaddr in resolved:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValidationError(
                "BLOCKED_URL", "URL resolves to a private/internal IP address"
            )

    return url


async def fetch_url_content(url: str, max_size: int = 50 * 1024 * 1024) -> bytes:
    """Download URL content with streaming byte counter, timeout, and post-redirect SSRF check."""
    await validate_url(url)
    async with httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=5,
        timeout=httpx.Timeout(connect=10, read=30),
    ) as client, client.stream("GET", url) as response:
        # Post-redirect SSRF check
        final_url = str(response.url)
        if final_url != url:
            await validate_url(final_url)
        response.raise_for_status()
        chunks = []
        total = 0
        async for chunk in response.aiter_bytes(8192):
            total += len(chunk)
            if total > max_size:
                raise ValidationError(
                    "CONTENT_TOO_LARGE", f"Content exceeds {max_size // (1024*1024)}MB limit"
                )
            chunks.append(chunk)
        return b"".join(chunks)
