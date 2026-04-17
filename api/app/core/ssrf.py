"""SSRF defences: blocklists, DNS resolve-and-pin, pinned-IP HTTP transport.

The threat model this module addresses:

- **Private/metadata targets**: resolver refuses hostnames that map to
  loopback, link-local, ULA, CGNAT, IPv4-mapped-IPv6, cloud metadata
  endpoints (AWS/GCP/Azure/Alibaba/DO), multicast, reserved, etc.
- **DNS rebinding**: ``resolve_and_pin(hostname)`` resolves once and
  returns the IP. Callers use :class:`PinnedDNSTransport` so the TCP
  connection is always made to the pinned IP, never re-resolving the
  hostname at connect time. The URL keeps the original hostname, so
  TLS SNI and the ``Host`` header stay correct.

The transport wraps the existing httpcore backend and only substitutes
the TCP ``host`` argument — it does not touch the URL, headers, or SSL
context. If httpcore's internal backend layout changes across versions,
construction raises loudly rather than silently disabling the pin.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any, NamedTuple

import httpx

from app.core.exceptions import ValidationError

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class URLValidation(NamedTuple):
    """Outcome of a URL validation pass."""

    url: str
    hostname: str
    pinned_ip: str


# ---------------------------------------------------------------------------
# Blocklists
# ---------------------------------------------------------------------------

# Rationale: ``ipaddress`` already flags loopback/link-local/private/reserved
# via ``ip.is_*``; these extras catch ranges those flags miss or that warrant
# a louder refusal.
BLOCKED_IPV4_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("0.0.0.0/8"),       # "this network"
    ipaddress.IPv4Network("100.64.0.0/10"),   # CGNAT (RFC 6598)
    ipaddress.IPv4Network("192.0.0.0/24"),    # IETF protocol assignments
    ipaddress.IPv4Network("198.18.0.0/15"),   # benchmarking (RFC 2544)
    ipaddress.IPv4Network("240.0.0.0/4"),     # reserved / future use
    ipaddress.IPv4Network("255.255.255.255/32"),  # limited broadcast
)

BLOCKED_IPV6_NETWORKS: tuple[ipaddress.IPv6Network, ...] = (
    ipaddress.IPv6Network("fc00::/7"),        # ULA
    ipaddress.IPv6Network("fe80::/10"),       # link-local
    ipaddress.IPv6Network("::ffff:0:0/96"),   # IPv4-mapped IPv6
    ipaddress.IPv6Network("64:ff9b::/96"),    # NAT64
    ipaddress.IPv6Network("2001:db8::/32"),   # documentation
)

# Hostnames that resolve to metadata endpoints on cloud providers.
# Checked before DNS resolution (the DNS answer itself can be hostile).
BLOCKED_HOSTS: frozenset[str] = frozenset(
    {
        "localhost",
        "ip6-localhost",
        "ip6-loopback",
        "broadcasthost",
        "::1",
        "metadata.google.internal",
        "metadata.internal",
        "metadata.azure.com",
        "metadata",
        "169.254.169.254",
        "fd00:ec2::254",       # AWS IMDS v6
        "100.100.100.200",     # Alibaba metadata
    }
)


def is_blocked_ip(ip: IPAddress) -> bool:
    """True if ``ip`` is loopback/private/metadata/reserved/etc.

    Covers the flags in :mod:`ipaddress` plus the explicit ranges in
    :data:`BLOCKED_IPV4_NETWORKS` / :data:`BLOCKED_IPV6_NETWORKS`, and
    recurses into the IPv4 embedded inside an IPv4-mapped IPv6 address.
    """
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True

    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in net for net in BLOCKED_IPV4_NETWORKS)

    # IPv6: peek at IPv4-mapped form recursively, then check v6 ranges.
    if ip.ipv4_mapped is not None and is_blocked_ip(ip.ipv4_mapped):
        return True
    return any(ip in net for net in BLOCKED_IPV6_NETWORKS)


def is_blocked_host(hostname: str) -> bool:
    """Literal-match against :data:`BLOCKED_HOSTS` (case-insensitive, strips brackets)."""
    cleaned = hostname.strip().strip("[]").lower()
    return cleaned in BLOCKED_HOSTS


async def resolve_and_pin(hostname: str) -> str:
    """Resolve ``hostname`` once and return a single safe IP string.

    All resolved addresses are checked — if any resolves to a blocked
    range, the whole request is rejected (the attacker may control DNS
    and return a mix to bypass partial filters). The first address is
    returned as the pinned IP.
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValidationError(
            "UNRESOLVABLE_URL", f"Cannot resolve hostname: {hostname}"
        ) from exc

    if not infos:
        raise ValidationError(
            "UNRESOLVABLE_URL", f"Cannot resolve hostname: {hostname}"
        )

    pinned: str | None = None
    for _, _, _, _, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if is_blocked_ip(ip):
            raise ValidationError(
                "BLOCKED_URL",
                "URL resolves to a private/internal IP address",
            )
        if pinned is None:
            pinned = str(ip)

    assert pinned is not None  # non-empty + no blocks ⇒ at least one IP
    return pinned


# ---------------------------------------------------------------------------
# Pinned DNS transport
# ---------------------------------------------------------------------------

class _PinnedBackend:
    """httpcore network backend that swaps one hostname for a pinned IP.

    Delegates everything else to the inner backend. The URL hostname is
    left untouched in the request (⇒ correct TLS SNI and ``Host`` header);
    only the TCP-level ``host`` argument passed to ``connect_tcp`` is
    rewritten, and only when it matches the hostname we pinned.
    """

    def __init__(self, pinned_ip: str, original_host: str, inner: Any) -> None:
        self._pinned_ip = pinned_ip
        self._original_host = original_host.lower()
        self._inner = inner

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        host_str = host.decode("ascii") if isinstance(host, bytes) else host
        if host_str.lower() == self._original_host:
            host = self._pinned_ip
        return await self._inner.connect_tcp(
            host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    # httpcore backends expose a handful of other coroutines (e.g. ``sleep``,
    # ``connect_unix_socket``). Forwarding via __getattr__ keeps us
    # forward-compatible if new hooks appear.
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class PinnedDNSTransport(httpx.AsyncHTTPTransport):
    """``httpx.AsyncHTTPTransport`` that pins DNS for a single hostname.

    Construct one per ``(hostname, ip)`` pair — reuse across hostnames
    would make the pin meaningless. Pass ``pinned_ip`` from a prior
    :func:`resolve_and_pin` call, and ``original_host`` from the URL
    being fetched.
    """

    def __init__(
        self, *, pinned_ip: str, original_host: str, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        pool = self._pool
        inner_backend = getattr(pool, "_network_backend", None)
        if inner_backend is None:  # pragma: no cover — httpcore layout guard
            raise RuntimeError(
                "PinnedDNSTransport: httpcore pool has no _network_backend "
                "— update the transport to match the installed httpcore."
            )
        pool._network_backend = _PinnedBackend(
            pinned_ip=pinned_ip,
            original_host=original_host,
            inner=inner_backend,
        )


__all__ = (
    "URLValidation",
    "BLOCKED_IPV4_NETWORKS",
    "BLOCKED_IPV6_NETWORKS",
    "BLOCKED_HOSTS",
    "is_blocked_ip",
    "is_blocked_host",
    "resolve_and_pin",
    "PinnedDNSTransport",
)
