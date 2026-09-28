# annas-api reference

Base URL: `https://annas.nestlone.com`

Every endpoint requires an API key unless noted:

```http
X-API-Key: <ANNAS_API_TOKEN>
```

Requests and responses are JSON (`Content-Type: application/json`). Errors use
the FastAPI shape `{"detail": "<message>"}`.

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/healthz` | none | Liveness probe. |
| `POST` | `/v1/search` | key | Enqueue a catalog search. |
| `POST` | `/v1/downloads` | key | Enqueue a download. |
| `GET` | `/v1/jobs` | key | List job summaries, newest first. |
| `GET` | `/v1/jobs/{job_id}` | key | Fetch one job in full. |
| `POST` | `/v1/jobs/{job_id}/cancel` | key | Cancel a queued job. |
| `GET` | `/v1/files/{job_id}` | signature | Download a completed file. |

---

## `GET /healthz`

No authentication. Returns `200 {"status": "ok"}` when the service is up.

---

## `POST /v1/search`

| Field | Type | Rules |
| --- | --- | --- |
| `query` | string | **Required**, 1–300 characters. |
| `ext` | string | Optional format filter (`epub`, `pdf`, `djvu`, …), ≤20 characters. |
| `limit` | integer | 1–50, default `10`. |

`202 Accepted`:

```json
{
  "id": "5c1f0a9e-...",
  "status": "queued",
  "status_url": "https://annas.nestlone.com/v1/jobs/5c1f0a9e-..."
}
```

When the job completes, `result` is an array of hits. Fields vary by catalog
record; the stable ones are:

| Field | Meaning |
| --- | --- |
| `md5` | Content digest — pass this to `/v1/downloads`. |
| `title` | Book title. |
| `format` | File format, when known. |

```json
{
  "id": "5c1f0a9e-...",
  "kind": "search",
  "status": "completed",
  "result": [
    {"md5": "bcdcd8bd16771a4f03c71b89a490dd53", "title": "…", "format": "epub"}
  ],
  "error": null,
  "created_at": 1790000000,
  "updated_at": 1790000012
}
```

---

## `POST /v1/downloads`

Exactly one of `md5` or `direct_url` is required. Supplying both, or neither, is
`422`.

| Field | Type | Rules |
| --- | --- | --- |
| `md5` | string | 32-character hex digest. |
| `direct_url` | string | Public HTTPS URL without credentials, ≤4096 characters. |
| `name` | string | Optional filename hint, ≤180 characters. |

Returns `202 Accepted` with the same body shape as a search job. The transfer
runs in the background and the file is published only after validating its
length, MD5 (when known), and — for PDFs — readability.

A completed download job adds `download_url` and leaves `result` as `null`:

```json
{
  "id": "9a2b…",
  "kind": "download",
  "status": "completed",
  "result": null,
  "error": null,
  "download_url": "https://annas.nestlone.com/v1/files/9a2b…?expires=1790000900&signature=…"
}
```

---

## `GET /v1/jobs`

Job summaries, newest first. Use this to inspect the queue or recover a job ID.

| Query | Default | Description |
| --- | --- | --- |
| `status` | — | One of `queued`, `running`, `completed`, `failed`, `cancelled`. Any other value is `422`. |
| `limit` | `50` | Page size, 1–100. |
| `offset` | `0` | Rows to skip, ≥0. |

```json
{
  "jobs": [
    {
      "id": "9a2b…",
      "kind": "download",
      "status": "queued",
      "error": null,
      "created_at": 1790000000,
      "updated_at": 1790000000
    }
  ],
  "count": 1,
  "limit": 50,
  "offset": 0
}
```

List items omit `result` so a page never carries a full result set. Completed
downloads still include `download_url`. When `count` equals `limit`, request the
next page with `offset += limit`.

---

## `GET /v1/jobs/{job_id}`

Returns the full job object described above, or `404` when the job never existed
or has been removed by the retention sweep.

---

## `POST /v1/jobs/{job_id}/cancel`

Cancels a job that has not started. A `running` job is never interrupted.

| Status | Meaning |
| --- | --- |
| `200` | Cancelled. Body is the job with `status: "cancelled"`. |
| `404` | No such job. |
| `409` | The job is `running` (`任务正在执行，无法取消；仅支持取消排队中的任务`) or already finished (`任务已结束，无法取消`). |

Cancellation is race-safe: whoever wins the transition (the worker starting the
job, or the cancel) settles the state, and a cancelled job is never revived.

---

## `GET /v1/files/{job_id}`

Signed delivery for a completed download. **No API key is required** — the
`expires` and `signature` query parameters are the credential.

| Query | Description |
| --- | --- |
| `expires` | Unix timestamp when the link stops working. |
| `signature` | HMAC-SHA256 over `"{job_id}:{expires}"`. |

- `200` — file bytes, with a `Content-Disposition` filename.
- `403` — missing, malformed, or expired signature.
- `404` — the job is unknown or not complete.

Do not construct this URL by hand; use the `download_url` returned by the job.
Links are valid for the deployment's configured TTL (15 minutes by default) and
should be treated as secrets.

---

## Job states

```
queued ──► running ──► completed
   │            └────► failed
   └─────────────────► cancelled
```

`completed`, `failed`, and `cancelled` are terminal. A deployment removes
terminal jobs — both the stored file and the database row — once its retention
window elapses (24 hours by default), after which lookups return `404`.

---

## Errors

| Status | Meaning |
| --- | --- |
| `401` | Missing or invalid API key. |
| `403` | Invalid or expired file signature. |
| `404` | Unknown job or file; or the job expired. |
| `409` | Job is running or already finished; cannot cancel. |
| `422` | Invalid field, MD5, URL, or query parameter. |
| `500` | Unexpected server error. |

---

## Python client

`scripts/annas_client.py` wraps this contract with no third-party dependencies.

| Method | Endpoint |
| --- | --- |
| `health()` | `GET /healthz` |
| `search(query, ext=None, limit=10)` | `POST /v1/search` |
| `download(md5=None, direct_url=None, name=None)` | `POST /v1/downloads` |
| `get_job(job_id)` | `GET /v1/jobs/{id}` |
| `list_jobs(status=None, limit=50, offset=0)` | `GET /v1/jobs` |
| `cancel(job_id)` | `POST /v1/jobs/{id}/cancel` |
| `wait(job_id, timeout=600, interval=1.0)` | poll to a terminal state |
| `run_search(...)` / `run_download(...)` | submit + wait |
| `fetch(url)` / `save(url, dest)` | signed file delivery |
| `run_search_to_file(query, dest, ...)` | search → download → save |

Failures raise `AnnasApiError`, whose `status` attribute carries the HTTP code
and whose message is the server's `detail` string.
