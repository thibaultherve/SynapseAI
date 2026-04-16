"""Key function for slowapi that is aware of trusted reverse proxies.

Why this exists
---------------
``slowapi.util.get_remote_address`` uses ``request.client.host`` unconditionally.
Behind a reverse proxy that is the proxy's IP — every client collapses onto a
single rate-limit bucket. Meanwhile, blindly trusting ``X-Forwarded-For`` lets
any client spoof the key by forging the header.

The compromise implemented here:

- Only read XFF when the direct peer is explicitly listed in
  ``settings.TRUSTED_PROXIES`` (IP or CIDR). Empty list = trust nothing
  (never "trust all").
- Walk the XFF chain **right to left**. The leftmost element is
  client-controlled and cannot be trusted; the rightmost elements were
  appended by proxies we trust. Return the first address that is *not*
  in the trusted set — that is the real client.
- Fallback safely to the direct peer when XFF is missing or malformed.
"""
from __future__ import annotations

import ipaddress
from functools import lru_cache

from starlette.requests import Request

from app.config import settings

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network
_UNKNOWN = "unknown"


@lru_cache(maxsize=1)
def _parse_networks(frozen: tuple[str, ...]) -> tuple[_Network, ...]:
    parsed: list[_Network] = []
    for raw in frozen:
        try:
            parsed.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue
    return tuple(parsed)


def _trusted_networks() -> tuple[_Network, ...]:
    return _parse_networks(tuple(settings.TRUSTED_PROXIES or ()))


def _is_trusted(addr_str: str, networks: tuple[_Network, ...]) -> bool:
    try:
        addr = ipaddress.ip_address(addr_str)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def _peer_host(request: Request) -> str:
    client = request.client
    if client and client.host:
        return client.host
    return _UNKNOWN


def trusted_client_ip(request: Request) -> str:
    """Return the address to key rate-limit buckets on.

    Direct peer if ``TRUSTED_PROXIES`` is empty, otherwise the rightmost
    XFF hop that is not itself a trusted proxy.
    """
    peer = _peer_host(request)
    networks = _trusted_networks()

    if not networks or not _is_trusted(peer, networks):
        return peer

    xff = request.headers.get("x-forwarded-for")
    if not xff:
        return peer

    hops = [hop.strip() for hop in xff.split(",") if hop.strip()]
    if not hops:
        return peer

    for hop in reversed(hops):
        if not _is_trusted(hop, networks):
            return hop

    # Every hop is a trusted proxy — use the leftmost as the best guess
    # at the real client (even though it's technically self-reported).
    return hops[0]
