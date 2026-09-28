"""Zero-dependency client for the annas-api HTTP service.

Only the standard library is used, so the client runs in a bare agent sandbox
with no ``pip install`` step. Configuration comes from the environment or the
constructor:

    ANNAS_API_BASE_URL   service root (default ``https://annas.nestlone.com``)
    ANNAS_API_TOKEN      value sent as the ``X-API-Key`` header

Typical use::

    from annas_client import AnnasClient

    client = AnnasClient()                       # domain + ANNAS_API_TOKEN
    job = client.run_search("The Great Gatsby", ext="epub", limit=5)
    for hit in job["result"]:
        print(hit["md5"], hit["title"])

    job = client.run_download(md5=hit["md5"])
    path = client.save(job["download_url"], ".")
    print("wrote", path)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://annas.nestlone.com"
DEFAULT_TIMEOUT = 60
DEFAULT_POLL_INTERVAL = 1.0
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
__version__ = "1.0.0"


class AnnasApiError(Exception):
    """Raised for transport failures and any non-2xx service response.

    ``status`` is the HTTP status code when the failure came from the service
    (``None`` for transport errors) and ``body`` holds the raw response bytes.
    """

    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


def _describe(status, raw):
    """Turn an error body into a short human message, preferring ``detail``."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, AttributeError):
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail:
            return detail
        if detail is not None:
            return json.dumps(detail, ensure_ascii=False)
    text = raw.decode("utf-8", "replace").strip() if raw else ""
    return text[:300] or "HTTP {}".format(status)


def _filename_from_headers(headers):
    disposition = headers.get("Content-Disposition") or headers.get("content-disposition")
    if not disposition:
        return None
    for part in disposition.split(";"):
        part = part.strip()
        if part.lower().startswith("filename="):
            return part.split("=", 1)[1].strip().strip('"')
    return None


class AnnasClient:
    """Thin synchronous wrapper over the annas-api REST contract."""

    def __init__(self, base_url=None, token=None, timeout=DEFAULT_TIMEOUT):
        resolved = base_url or os.environ.get("ANNAS_API_BASE_URL") or DEFAULT_BASE_URL
        self.base_url = resolved.rstrip("/")
        self.token = token if token is not None else os.environ.get("ANNAS_API_TOKEN")
        self.timeout = timeout

    # -- transport ---------------------------------------------------------

    def _url(self, path):
        if path.startswith(("http://", "https://")):
            return path
        return self.base_url + path

    def _request(self, method, path, params=None, body=None, auth=True):
        """Perform one request and return ``(status, headers, body_bytes)``."""
        url = self._url(path)
        if params:
            url += "?" + urlencode(params)

        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            if not self.token:
                raise AnnasApiError(
                    "no API token configured; set ANNAS_API_TOKEN or pass token="
                )
            headers["X-API-Key"] = self.token

        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as exc:
            raw = exc.read()
            raise AnnasApiError(_describe(exc.code, raw), status=exc.code, body=raw) from exc
        except URLError as exc:
            raise AnnasApiError("cannot reach {}: {}".format(url, exc.reason)) from exc

    def _json(self, method, path, **kwargs):
        _, _, raw = self._request(method, path, **kwargs)
        return json.loads(raw.decode("utf-8")) if raw else None

    # -- endpoints ---------------------------------------------------------

    def health(self):
        """``GET /healthz`` — no authentication required."""
        return self._json("GET", "/healthz", auth=False)

    def search(self, query, ext=None, limit=10):
        """``POST /v1/search`` — enqueue a catalog search, returns the job."""
        body = {"query": query, "limit": limit}
        if ext:
            body["ext"] = ext
        return self._json("POST", "/v1/search", body=body)

    def download(self, md5=None, direct_url=None, name=None):
        """``POST /v1/downloads`` — enqueue a download from md5 *or* direct_url."""
        if bool(md5) == bool(direct_url):
            raise ValueError("provide exactly one of md5 or direct_url")
        body = {"md5": md5} if md5 else {"direct_url": direct_url}
        if name:
            body["name"] = name
        return self._json("POST", "/v1/downloads", body=body)

    def get_job(self, job_id):
        """``GET /v1/jobs/{job_id}`` — current job state and result."""
        return self._json("GET", "/v1/jobs/{}".format(quote(job_id)))

    def list_jobs(self, status=None, limit=50, offset=0):
        """``GET /v1/jobs`` — newest-first queue page (summaries, no ``result``)."""
        params = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        return self._json("GET", "/v1/jobs", params=params)

    def cancel(self, job_id):
        """``POST /v1/jobs/{job_id}/cancel`` — only a ``queued`` job can be cancelled."""
        return self._json("POST", "/v1/jobs/{}/cancel".format(quote(job_id)))

    # -- orchestration -----------------------------------------------------

    def wait(self, job_id, timeout=600, interval=DEFAULT_POLL_INTERVAL, on_poll=None):
        """Poll until the job reaches a terminal state or ``timeout`` elapses.

        ``on_poll`` receives each intermediate job dict, which is handy for
        progress logging. The terminal job dict is returned either way, so the
        caller should check ``job["status"]`` before using ``result``.
        """
        deadline = time.monotonic() + timeout
        while True:
            job = self.get_job(job_id)
            if job["status"] in TERMINAL_STATUSES:
                return job
            if on_poll:
                on_poll(job)
            if time.monotonic() >= deadline:
                raise AnnasApiError(
                    "job {} still {} after {}s".format(job_id, job["status"], timeout)
                )
            time.sleep(interval)

    def run_search(self, query, ext=None, limit=10, timeout=600, **wait_kwargs):
        """Submit a search and poll it to completion."""
        return self.wait(self.search(query, ext=ext, limit=limit)["id"], timeout, **wait_kwargs)

    def run_download(self, md5=None, direct_url=None, name=None, timeout=1800, **wait_kwargs):
        """Submit a download and poll it to completion."""
        job_id = self.download(md5=md5, direct_url=direct_url, name=name)["id"]
        return self.wait(job_id, timeout, **wait_kwargs)

    # -- file delivery -----------------------------------------------------

    def fetch(self, url):
        """Fetch a signed file URL (no API key) and return ``(bytes, headers)``."""
        _, headers, raw = self._request("GET", url, auth=False)
        return raw, headers

    def save(self, url, dest):
        """Fetch a signed file URL and write it to ``dest``.

        ``dest`` may be a file path or an existing directory, in which case the
        server-provided filename is used. Returns the written :class:`Path`.
        """
        raw, headers = self.fetch(url)
        target = Path(dest)
        if target.is_dir():
            target = target / (_filename_from_headers(headers) or "download.bin")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return target

    def run_search_to_file(self, query, dest, ext=None, limit=10, **kwargs):
        """Search, download the first hit, and save it — the common happy path."""
        job = self.run_search(query, ext=ext, limit=limit, **kwargs)
        if job["status"] != "completed":
            raise AnnasApiError("search {}: {}".format(job["status"], job.get("error")))
        if not job.get("result"):
            raise AnnasApiError("search returned no results for {!r}".format(query))
        book = job["result"][0]
        download = self.run_download(md5=book["md5"])
        if download["status"] != "completed":
            raise AnnasApiError("download {}: {}".format(download["status"], download.get("error")))
        return self.save(download["download_url"], dest), book
