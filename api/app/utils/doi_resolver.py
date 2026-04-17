"""DOI → canonical URL resolution with per-hop SSRF validation.

``doi.org`` almost always redirects at least twice. We follow the chain
manually (``follow_redirects=False``) so each hop goes through
:func:`validate_url` and a fresh :class:`PinnedDNSTransport`, which
stops an attacker-controlled redirect target that resolves to a private
IP from being fetched.
"""
from __future__ import annotations

import httpx

from app.core.exceptions import ValidationError
from app.core.ssrf import PinnedDNSTransport
from app.utils.url_validator import MAX_REDIRECTS, validate_url

DOI_BASE = "https://doi.org/"


async def resolve_doi(doi: str) -> str:
    """Resolve a DOI by following doi.org redirects, one hop at a time."""
    current_url = f"{DOI_BASE}{doi}"
    visited: set[str] = set()

    for _ in range(MAX_REDIRECTS + 1):
        if current_url in visited:
            raise ValidationError(
                "DOI_RESOLUTION_FAILED", "DOI redirect loop detected"
            )
        visited.add(current_url)

        validation = await validate_url(current_url)
        transport = PinnedDNSTransport(
            pinned_ip=validation.pinned_ip,
            original_host=validation.hostname,
        )
        try:
            async with httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                timeout=httpx.Timeout(15.0, connect=10.0),
            ) as client:
                try:
                    response = await client.head(current_url)
                except httpx.HTTPError as exc:
                    raise ValidationError(
                        "DOI_RESOLUTION_FAILED", "Could not resolve DOI"
                    ) from exc

                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValidationError(
                            "DOI_RESOLUTION_FAILED",
                            "DOI redirect missing Location header",
                        )
                    current_url = str(httpx.URL(current_url).join(location))
                    continue

                try:
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise ValidationError(
                        "DOI_RESOLUTION_FAILED", "Could not resolve DOI"
                    ) from exc
                return current_url
        finally:
            pass

    raise ValidationError(
        "DOI_RESOLUTION_FAILED", "Too many redirects while resolving DOI"
    )
