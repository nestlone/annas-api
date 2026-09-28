"""Rotating HTTP proxy pool backed by an external provider API.

The provider returns one ``ip:port`` per request (plain text). Each ``acquire``
fetches a fresh proxy so that concurrent jobs never share an exit IP; callers
that need one IP for several steps (e.g. browser resolve + download) hold the
returned value for the lifetime of that job.
"""

import json
import re

import requests

# Providers return "ip:port" on success and a JSON error object (e.g. an
# exhausted-traffic notice) on failure. Match the shape strictly so an error
# payload is rejected rather than handed downstream as a bogus proxy.
_ENDPOINT = re.compile(r"^(?P<host>[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?):(?P<port>\d{1,5})$")


class ProxyPool:
    """Fetches a fresh HTTP proxy from a provider endpoint on each call."""

    def __init__(self, api_url, scheme="http", timeout=8.0):
        self.api_url = api_url
        self.scheme = scheme or "http"
        self.timeout = timeout

    def _fetch(self):
        response = requests.get(self.api_url, timeout=self.timeout)
        response.raise_for_status()
        text = (response.text or "").strip()
        endpoint = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if endpoint.startswith("{"):
            try:
                payload = json.loads(endpoint)
            except ValueError:
                payload = {}
            detail = payload.get("msg") or payload.get("code") or text[:120]
            raise RuntimeError(f"代理池拒绝了请求: {detail}")
        match = _ENDPOINT.match(endpoint)
        if not match or not 1 <= int(match.group("port")) <= 65535:
            raise RuntimeError(f"代理池返回异常: {text[:120]!r}")
        return f"{self.scheme}://{match.group('host')}:{match.group('port')}"

    def acquire(self, rotate=False):
        """Return a fresh proxy. ``rotate`` is accepted for readability at retry sites."""
        return self._fetch()

    def rotate(self):
        """Fetch another proxy, used to retry after rate limiting."""
        return self._fetch()


_POOL = None


def get_pool(config):
    """Return a process-wide pool for the configured provider URL, or None."""
    global _POOL
    url = (config or {}).get("proxy_pool_url")
    if not url:
        return None
    scheme = (config or {}).get("proxy_pool_scheme") or "http"
    if _POOL is None or _POOL.api_url != url or _POOL.scheme != scheme:
        _POOL = ProxyPool(url, scheme=scheme)
    return _POOL
