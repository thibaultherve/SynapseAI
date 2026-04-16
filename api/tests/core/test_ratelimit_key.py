"""Tests for trusted_client_ip: XFF handling behind configured proxies."""
from __future__ import annotations

import pytest
from starlette.requests import Request

from app.config import settings
from app.core.ratelimit_key import trusted_client_ip


def _request(client_ip: str, xff: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (client_ip, 0),
        "query_string": b"",
    }
    return Request(scope)


@pytest.fixture
def clear_trusted_proxies(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", [])
    return settings


@pytest.fixture
def trust_private_range(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["10.0.0.0/8"])
    return settings


def test_empty_trusted_proxies_returns_peer(clear_trusted_proxies):
    req = _request("203.0.113.5", xff="1.2.3.4, 5.6.7.8")
    assert trusted_client_ip(req) == "203.0.113.5"


def test_untrusted_peer_ignores_xff(trust_private_range):
    # Peer is public (not in trusted range) — any XFF is attacker-supplied.
    req = _request("203.0.113.5", xff="spoofed-client")
    assert trusted_client_ip(req) == "203.0.113.5"


def test_trusted_peer_uses_rightmost_untrusted_hop(trust_private_range):
    # Chain reads left → right as client → edge → internal, so the rightmost
    # untrusted hop (5.6.7.8) is the real client.
    req = _request("10.0.0.1", xff="1.2.3.4, 5.6.7.8, 10.0.0.2")
    assert trusted_client_ip(req) == "5.6.7.8"


def test_trusted_peer_no_xff_falls_back_to_peer(trust_private_range):
    req = _request("10.0.0.1", xff=None)
    assert trusted_client_ip(req) == "10.0.0.1"


def test_trusted_peer_all_hops_trusted_returns_leftmost(trust_private_range):
    # Every hop sits inside our trusted range — best guess at the real
    # client is the leftmost, even if it's self-reported.
    req = _request("10.0.0.1", xff="10.0.0.5, 10.0.0.3, 10.0.0.2")
    assert trusted_client_ip(req) == "10.0.0.5"


def test_malformed_xff_entries_skipped(trust_private_range):
    req = _request("10.0.0.1", xff="not-an-ip, 203.0.113.5, 10.0.0.2")
    # "not-an-ip" is not a valid IP → _is_trusted returns False, so the
    # rightmost scan picks 203.0.113.5 (not trusted, valid-ish string).
    assert trusted_client_ip(req) == "203.0.113.5"


def test_empty_xff_string_falls_back_to_peer(trust_private_range):
    req = _request("10.0.0.1", xff="   ")
    assert trusted_client_ip(req) == "10.0.0.1"


def test_missing_client_returns_unknown(clear_trusted_proxies):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": None,
        "query_string": b"",
    }
    req = Request(scope)
    assert trusted_client_ip(req) == "unknown"
