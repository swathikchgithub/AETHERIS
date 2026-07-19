"""Unit tests for the SSRF-blocking sandbox validator (OWASP A10).

Pure unit tests — no network access required: numeric-IP-literal cases
resolve locally without a DNS round trip, and every other case is expected
to raise before any socket is opened.
"""
from __future__ import annotations

import pytest

from aetheris.tools.sandbox import SSRFBlockedError, validate_url


def test_validate_url_rejects_http_scheme():
    with pytest.raises(SSRFBlockedError):
        validate_url("http://example.com/api", domain_allowlist=frozenset())


def test_validate_url_rejects_loopback():
    with pytest.raises(SSRFBlockedError):
        validate_url("https://127.0.0.1/api", domain_allowlist=frozenset())


def test_validate_url_rejects_cloud_metadata_ip():
    with pytest.raises(SSRFBlockedError):
        validate_url("https://169.254.169.254/latest/meta-data/", domain_allowlist=frozenset())


def test_validate_url_rejects_private_network_10_range():
    with pytest.raises(SSRFBlockedError):
        validate_url("https://10.0.0.5/internal", domain_allowlist=frozenset())


def test_validate_url_rejects_private_network_192_range():
    with pytest.raises(SSRFBlockedError):
        validate_url("https://192.168.1.1/internal", domain_allowlist=frozenset())


def test_validate_url_rejects_domain_outside_tenant_allowlist():
    with pytest.raises(SSRFBlockedError):
        validate_url(
            "https://evil.example.com/api",
            domain_allowlist=frozenset({"api.partner.example.com"}),
        )


def test_validate_url_rejects_missing_hostname():
    with pytest.raises(SSRFBlockedError):
        validate_url("https:///no-host", domain_allowlist=frozenset())


def test_validate_url_allows_allowlisted_public_ip_literal():
    # A numeric literal short-circuits DNS, keeping this test hermetic.
    validate_url("https://1.1.1.1/api", domain_allowlist=frozenset({"1.1.1.1"}))
