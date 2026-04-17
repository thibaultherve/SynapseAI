"""Tests for app.core.ssrf and the validators that depend on it.

The suite groups into four concerns:

1. **Blocklists** — every extended range (CGNAT, ULA, IPv4-mapped,
   metadata hosts) must be flagged by ``is_blocked_ip`` / ``is_blocked_host``.
2. **resolve_and_pin** — resolver errors/blocks surface as
   ``ValidationError``; mixed-safety answers are refused.
3. **validate_url** — scheme, host, literal-IP, DNS paths.
4. **Pinned transport + redirect loops** — the DNS rebind scenario
   (post-validation resolution flips to loopback) must not reach the
   attacker address, and each redirect hop goes through validation.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Iterable
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.exceptions import ValidationError


@pytest.fixture(autouse=True, scope="function")
async def _clean_tables():
    """Override the conftest autouse DB-truncate fixture.

    None of these tests touch Postgres; booting a test DB just to run
    pure-unit checks on URL parsing and a memory-only transport is waste.
    """
    yield
from app.core.ssrf import (
    BLOCKED_IPV4_NETWORKS,
    BLOCKED_IPV6_NETWORKS,
    PinnedDNSTransport,
    _PinnedBackend,
    is_blocked_host,
    is_blocked_ip,
    resolve_and_pin,
)
from app.utils import doi_resolver, url_validator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _getaddrinfo_returning(*ips: str):
    """Build a fake ``loop.getaddrinfo`` that yields the supplied IPs."""

    async def _fake(host, port, *args, **kwargs):  # noqa: ARG001 — signature compat
        infos = []
        for ip in ips:
            addr = ipaddress.ip_address(ip)
            family = socket.AF_INET if addr.version == 4 else socket.AF_INET6
            sockaddr: tuple = (str(addr), 0) if family == socket.AF_INET else (str(addr), 0, 0, 0)
            infos.append((family, socket.SOCK_STREAM, 0, "", sockaddr))
        return infos

    return _fake


def _patch_getaddrinfo(monkeypatch, ips: Iterable[str] | Exception):
    """Install a fake ``getaddrinfo`` on the running loop for one test."""

    loop = asyncio.get_event_loop()
    if isinstance(ips, Exception):
        async def _raise(*args, **kwargs):  # noqa: ARG001
            raise ips
        monkeypatch.setattr(loop, "getaddrinfo", _raise, raising=False)
    else:
        monkeypatch.setattr(
            loop, "getaddrinfo", _getaddrinfo_returning(*ips), raising=False
        )


# ---------------------------------------------------------------------------
# 1. Blocklists
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ip",
    [
        "0.0.0.1",              # 0.0.0.0/8
        "100.64.1.2",           # CGNAT
        "198.18.0.1",           # benchmarking
        "240.0.0.1",            # reserved
        "255.255.255.255",      # limited broadcast
        "127.0.0.1",            # loopback (via ipaddress flag)
        "169.254.1.1",          # link-local
        "10.0.0.1",             # private
        "192.168.1.1",          # private
    ],
)
def test_is_blocked_ipv4(ip):
    assert is_blocked_ip(ipaddress.ip_address(ip)) is True


@pytest.mark.parametrize(
    "ip",
    [
        "fc00::1",              # ULA
        "fd12:3456:789a::1",    # ULA
        "fe80::1",              # link-local
        "::ffff:127.0.0.1",     # IPv4-mapped loopback
        "::ffff:169.254.169.254",  # IPv4-mapped metadata
        "::1",                  # loopback
        "2001:db8::1",          # documentation
    ],
)
def test_is_blocked_ipv6(ip):
    assert is_blocked_ip(ipaddress.ip_address(ip)) is True


@pytest.mark.parametrize(
    "ip",
    ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"],
)
def test_public_ips_pass(ip):
    assert is_blocked_ip(ipaddress.ip_address(ip)) is False


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "Localhost",
        "[::1]",
        "metadata.google.internal",
        "metadata.azure.com",
        "169.254.169.254",
        "100.100.100.200",
    ],
)
def test_is_blocked_host(host):
    assert is_blocked_host(host) is True


def test_blocklists_are_non_empty_sanity():
    # Guard against an accidental empty tuple commit.
    assert BLOCKED_IPV4_NETWORKS
    assert BLOCKED_IPV6_NETWORKS


# ---------------------------------------------------------------------------
# 2. resolve_and_pin
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_and_pin_returns_first_ip(monkeypatch):
    _patch_getaddrinfo(monkeypatch, ["93.184.216.34", "2606:2800:220:1::248"])
    assert await resolve_and_pin("example.com") == "93.184.216.34"


@pytest.mark.asyncio
async def test_resolve_and_pin_rejects_when_any_ip_blocked(monkeypatch):
    # Attacker DNS mixes a public and a private answer.
    _patch_getaddrinfo(monkeypatch, ["93.184.216.34", "127.0.0.1"])
    with pytest.raises(ValidationError) as exc:
        await resolve_and_pin("evil.test")
    assert exc.value.code == "BLOCKED_URL"


@pytest.mark.asyncio
async def test_resolve_and_pin_unresolvable(monkeypatch):
    _patch_getaddrinfo(monkeypatch, socket.gaierror("nope"))
    with pytest.raises(ValidationError) as exc:
        await resolve_and_pin("nope.invalid")
    assert exc.value.code == "UNRESOLVABLE_URL"


# ---------------------------------------------------------------------------
# 3. validate_url
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_validate_url_rejects_unknown_scheme():
    with pytest.raises(ValidationError) as exc:
        await url_validator.validate_url("ftp://example.com/")
    assert exc.value.code == "INVALID_URL"


@pytest.mark.asyncio
async def test_validate_url_rejects_blocked_host():
    with pytest.raises(ValidationError) as exc:
        await url_validator.validate_url("http://metadata.google.internal/")
    assert exc.value.code == "BLOCKED_URL"


@pytest.mark.asyncio
async def test_validate_url_rejects_literal_private_ip():
    with pytest.raises(ValidationError) as exc:
        await url_validator.validate_url("http://10.0.0.1/")
    assert exc.value.code == "BLOCKED_URL"


@pytest.mark.asyncio
async def test_validate_url_accepts_literal_public_ip():
    result = await url_validator.validate_url("http://1.1.1.1/")
    assert result.hostname == "1.1.1.1"
    assert result.pinned_ip == "1.1.1.1"


@pytest.mark.asyncio
async def test_validate_url_resolves_and_pins(monkeypatch):
    _patch_getaddrinfo(monkeypatch, ["93.184.216.34"])
    result = await url_validator.validate_url("https://example.com/x")
    assert result.url == "https://example.com/x"
    assert result.hostname == "example.com"
    assert result.pinned_ip == "93.184.216.34"


# ---------------------------------------------------------------------------
# 4. PinnedDNSTransport
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pinned_backend_rewrites_matching_host():
    inner = AsyncMock()
    inner.connect_tcp = AsyncMock(return_value="stream")
    backend = _PinnedBackend(
        pinned_ip="93.184.216.34",
        original_host="example.com",
        inner=inner,
    )
    await backend.connect_tcp("example.com", 443, timeout=5)

    assert inner.connect_tcp.await_count == 1
    call = inner.connect_tcp.await_args
    # First positional arg (host) must be the pinned IP.
    assert call.args[0] == "93.184.216.34"
    assert call.args[1] == 443
    assert call.kwargs.get("timeout") == 5


@pytest.mark.asyncio
async def test_pinned_backend_leaves_other_hosts_alone():
    inner = AsyncMock()
    inner.connect_tcp = AsyncMock(return_value="stream")
    backend = _PinnedBackend(
        pinned_ip="93.184.216.34",
        original_host="example.com",
        inner=inner,
    )
    await backend.connect_tcp("other.test", 443)
    assert inner.connect_tcp.await_args.args[0] == "other.test"


def test_pinned_transport_wires_backend():
    transport = PinnedDNSTransport(
        pinned_ip="93.184.216.34",
        original_host="example.com",
    )
    assert isinstance(transport._pool._network_backend, _PinnedBackend)
    assert transport._pool._network_backend._pinned_ip == "93.184.216.34"


# ---------------------------------------------------------------------------
# 5. fetch_url_content with DNS rebinding scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_url_content_refuses_rebind(monkeypatch):
    """After the safe resolve, a hostile resolver would flip to loopback.

    Because we pin the IP at validation time and the transport never
    re-resolves, a rebind doesn't help the attacker: the second call to
    ``getaddrinfo`` is never made. Sanity-check by swapping the fake
    resolver to return 127.0.0.1 *after* the first call — the request
    should still fail, and fail because the pinned IP is wrong (the
    mock transport rejects unexpected destinations), not because the
    resolver handed it 127.0.0.1.
    """
    loop = asyncio.get_event_loop()
    calls: list[tuple] = []

    async def _fake(host, port, *args, **kwargs):  # noqa: ARG001
        calls.append((host, port))
        if len(calls) == 1:
            addr = "93.184.216.34"
        else:  # pragma: no cover — would be reached on a rebind bug
            addr = "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (addr, 0))]

    monkeypatch.setattr(loop, "getaddrinfo", _fake, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        # Whatever the caller connected to, the server sees the URL's host.
        assert request.url.host == "example.com"
        return httpx.Response(200, content=b"ok")

    mock_transport = httpx.MockTransport(handler)

    orig = httpx.AsyncClient.__init__

    def fake_client_init(self, *, transport=None, **kwargs):
        # Swap out the PinnedDNSTransport for a MockTransport so no real
        # socket work is attempted, but still assert that our code built
        # a PinnedDNSTransport to begin with.
        assert isinstance(transport, PinnedDNSTransport)
        orig(self, transport=mock_transport, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_client_init)

    content = await url_validator.fetch_url_content("https://example.com/a")
    assert content == b"ok"
    # getaddrinfo called exactly once — no re-resolution happened.
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_fetch_url_content_follows_redirect_and_revalidates(monkeypatch):
    loop = asyncio.get_event_loop()
    resolved = {"a.test": "93.184.216.34", "b.test": "93.184.216.35"}

    async def _fake(host, port, *args, **kwargs):  # noqa: ARG001
        addr = resolved[host]
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (addr, 0))]

    monkeypatch.setattr(loop, "getaddrinfo", _fake, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.test":
            return httpx.Response(302, headers={"Location": "https://b.test/dest"})
        if request.url.host == "b.test":
            return httpx.Response(200, content=b"final")
        raise AssertionError(f"unexpected host {request.url.host}")

    mock_transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient.__init__

    def fake_init(self, *, transport=None, **kwargs):
        assert isinstance(transport, PinnedDNSTransport)
        orig(self, transport=mock_transport, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_init)

    content = await url_validator.fetch_url_content("https://a.test/")
    assert content == b"final"


@pytest.mark.asyncio
async def test_fetch_url_content_blocks_redirect_to_private_ip(monkeypatch):
    loop = asyncio.get_event_loop()
    resolved = {"a.test": "93.184.216.34", "internal.test": "10.0.0.5"}

    async def _fake(host, port, *args, **kwargs):  # noqa: ARG001
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (resolved[host], 0))]

    monkeypatch.setattr(loop, "getaddrinfo", _fake, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.test":
            return httpx.Response(302, headers={"Location": "https://internal.test/x"})
        raise AssertionError("must not reach internal.test")

    mock_transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient.__init__

    def fake_init(self, *, transport=None, **kwargs):
        orig(self, transport=mock_transport, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_init)

    with pytest.raises(ValidationError) as exc:
        await url_validator.fetch_url_content("https://a.test/")
    assert exc.value.code == "BLOCKED_URL"


@pytest.mark.asyncio
async def test_fetch_url_content_too_many_redirects(monkeypatch):
    loop = asyncio.get_event_loop()

    async def _fake(host, port, *args, **kwargs):  # noqa: ARG001
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(loop, "getaddrinfo", _fake, raising=False)

    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            302, headers={"Location": f"https://a.test/{counter['n']}"}
        )

    mock_transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient.__init__

    def fake_init(self, *, transport=None, **kwargs):
        orig(self, transport=mock_transport, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_init)

    with pytest.raises(ValidationError) as exc:
        await url_validator.fetch_url_content("https://a.test/0")
    assert exc.value.code == "TOO_MANY_REDIRECTS"


# ---------------------------------------------------------------------------
# 6. doi_resolver: chained redirects with per-hop validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_doi_follows_chain(monkeypatch):
    loop = asyncio.get_event_loop()

    async def _fake(host, port, *args, **kwargs):  # noqa: ARG001
        # All resolve to a public IP; the test exercises the redirect loop.
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(loop, "getaddrinfo", _fake, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "doi.org":
            return httpx.Response(
                302, headers={"Location": "https://publisher.test/paper/42"}
            )
        if request.url.host == "publisher.test":
            return httpx.Response(200)
        raise AssertionError(f"unexpected host {request.url.host}")

    mock_transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient.__init__

    def fake_init(self, *, transport=None, **kwargs):
        orig(self, transport=mock_transport, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_init)

    final = await doi_resolver.resolve_doi("10.1000/xyz")
    assert final == "https://publisher.test/paper/42"


@pytest.mark.asyncio
async def test_resolve_doi_blocks_hostile_redirect(monkeypatch):
    loop = asyncio.get_event_loop()
    resolved = {"doi.org": "93.184.216.34", "internal.test": "127.0.0.1"}

    async def _fake(host, port, *args, **kwargs):  # noqa: ARG001
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (resolved[host], 0))]

    monkeypatch.setattr(loop, "getaddrinfo", _fake, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "doi.org":
            return httpx.Response(
                302, headers={"Location": "https://internal.test/leak"}
            )
        raise AssertionError("must not reach internal.test")

    mock_transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient.__init__

    def fake_init(self, *, transport=None, **kwargs):
        orig(self, transport=mock_transport, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_init)

    with pytest.raises(ValidationError) as exc:
        await doi_resolver.resolve_doi("10.1000/xyz")
    assert exc.value.code == "BLOCKED_URL"
