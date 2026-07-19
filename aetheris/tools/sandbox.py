"""Sandboxed HTTP executor for dynamic admin-defined tools.

This module is the in-process validation layer of a defense-in-depth
strategy against SSRF (OWASP A10). It is deliberately *not* the only
line of defense — see the module-level note on `SandboxedHTTPExecutor`
for the deployment-layer backstop this assumes.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

# RFC1918 private ranges, loopback, link-local (includes the 169.254.169.254
# cloud-metadata endpoint on AWS/GCP/Azure/OCI), CGNAT, and IPv6 equivalents.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network(net)
    for net in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "100.64.0.0/10",
        "0.0.0.0/8",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
]

_ALLOWED_SCHEMES = {"https"}


class SSRFBlockedError(Exception):
    """Raised whenever a dynamic tool call targets a disallowed URL."""


def _resolve_and_validate(hostname: str) -> None:
    """Resolves DNS ourselves and checks the concrete IP(s) — validating the
    hostname string alone is insufficient against DNS rebinding, where a
    domain resolves to a public IP at allowlist-check time and a private
    one at connect time."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SSRFBlockedError(f"DNS resolution failed for {hostname!r}") from exc
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if any(ip in net for net in _BLOCKED_NETWORKS):
            raise SSRFBlockedError(f"{hostname!r} resolves to a blocked address ({ip})")


def validate_url(url: str, *, domain_allowlist: frozenset[str]) -> None:
    """Validates scheme, tenant domain allowlist, and resolved IP.

    Time: O(h + b) where h is DNS answer count and b is the blocked-network
    list size (small, fixed). Space: O(1).
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SSRFBlockedError(f"scheme {parsed.scheme!r} is not allowed")
    if not parsed.hostname:
        raise SSRFBlockedError("URL has no hostname")
    if domain_allowlist and parsed.hostname not in domain_allowlist:
        raise SSRFBlockedError(f"{parsed.hostname!r} is not in the tenant allowlist")
    _resolve_and_validate(parsed.hostname)


class SandboxedHTTPExecutor:
    """Executes a single admin-defined tool call with SSRF blocking, no
    redirect-follow, a hard timeout, and a response-size cap.

    Deployment note (the layer this class assumes, not implements): run the
    process hosting this executor in an egress-restricted network sandbox —
    either a container with a NetworkPolicy/firewall permitting only the
    tenant's allowlisted domains, or a gVisor/Firecracker-isolated worker
    pool dedicated to outbound tool calls. That way a bug in this in-process
    validator is not a single point of failure for SSRF protection
    (defense in depth, OWASP A10).
    """

    MAX_RESPONSE_BYTES = 1_000_000

    def __init__(self, domain_allowlist: frozenset[str]) -> None:
        self._domain_allowlist = domain_allowlist

    def execute(
        self,
        *,
        url: str,
        method: str,
        headers: dict[str, str],
        json_body: dict | None,
        timeout_seconds: float,
    ) -> dict:
        validate_url(url, domain_allowlist=self._domain_allowlist)
        with httpx.Client(follow_redirects=False, timeout=timeout_seconds) as client:
            response = client.request(method, url, headers=headers, json=json_body)
            if response.status_code in (301, 302, 303, 307, 308):
                raise SSRFBlockedError("redirects are not followed")
            content = response.content[: self.MAX_RESPONSE_BYTES]
        return {
            "status_code": response.status_code,
            "body": content.decode("utf-8", errors="replace"),
        }
