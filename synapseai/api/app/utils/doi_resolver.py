import httpx

from app.core.exceptions import ValidationError
from app.utils.url_validator import validate_url

DOI_BASE = "https://doi.org/"


async def resolve_doi(doi: str) -> str:
    """Resolve DOI to final URL via doi.org redirect."""
    doi_url = f"{DOI_BASE}{doi}"
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=5,
            timeout=httpx.Timeout(connect=10, read=15),
        ) as client:
            response = await client.head(doi_url)
            response.raise_for_status()
            final_url = str(response.url)
    except httpx.HTTPError as exc:
        raise ValidationError("DOI_RESOLUTION_FAILED", "Could not resolve DOI") from exc

    # SSRF check on the resolved URL
    await validate_url(final_url)
    return final_url
