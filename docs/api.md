# HTTP API reference

Base URL: `http://127.0.0.1:8000`. Interactive spec: `/docs`.

Every endpoint except `GET /healthz` and the signed file endpoint requires an API key:

```http
X-API-Key: <FERRY_API_TOKEN>
```

## Health

`GET /healthz` → `{"status":"ok"}`

## Create a search job

`POST /v1/search`

| Field | Type | Rules |
| --- | --- | --- |
| `query` | string | Required, 1–300 characters. |
| `ext` | string | Optional format filter, e.g. `epub`. |
| `limit` | integer | 1–50, default `10`. |

`202 Accepted`:

```json
{
  "id": "<job-id>",
  "status": "queued",
  "status_url": "http://127.0.0.1:8000/v1/jobs/<job-id>"
}
```

## Create a download job

`POST /v1/downloads`

Exactly one of `md5` or `direct_url` is required.

| Field | Type | Rules |
| --- | --- | --- |
| `md5` | string | 32-character hex digest. |
| `direct_url` | string | Public HTTPS URL without credentials. |
| `name` | string | Optional filename, ≤180 characters. |

Returns `202 Accepted` with the same body shape as a search job. The file is
validated in the background and only published on success.

## Get a job

`GET /v1/jobs/{job_id}`

States: `queued`, `running`, `completed`, `failed`, `cancelled`. Only `queued` jobs
can be cancelled. A completed search puts a list in `result`; a completed download
adds a `download_url`.

```json
{
  "id": "<job-id>",
  "kind": "download",
  "status": "completed",
  "result": null,
  "error": null,
  "download_url": "http://127.0.0.1:8000/v1/files/<job-id>?expires=...&signature=..."
}
```

The download URL is time-limited and needs no API key. Treat it as a sensitive
temporary credential and keep it out of public logs.

## List jobs

`GET /v1/jobs`

Returns job summaries newest-first, for inspecting the queue.

| Query | Default | Description |
| --- | --- | --- |
| `status` | — | Filter by state. |
| `limit` | `50` | Page size, 1–100. |
| `offset` | `0` | Rows to skip. |

```json
{
  "jobs": [
    {
      "id": "<job-id>",
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

List items omit `result`, so a page never carries a full result set; completed
downloads still include `download_url`. When `count` equals `limit`, advance by
adding `limit` to `offset`.

## Cancel a job

`POST /v1/jobs/{job_id}/cancel`

Cancels a job that has not started. A `running` job cannot be interrupted.

- `200` — cancelled (`status: "cancelled"`).
- `409` — the job is running or already finished.
- `404` — no such job.

## Errors

| Status | Meaning |
| --- | --- |
| `401` | Missing or invalid API key. |
| `403` | Invalid or expired download signature. |
| `404` | Unknown job, file, or delivery state. |
| `409` | Job is running or finished; cannot cancel. |
| `422` | Invalid field, MD5, URL, or parameter. |
| `500` | Unexpected server error. |

## Example

```bash
TOKEN=$FERRY_API_TOKEN
curl -X POST http://127.0.0.1:8000/v1/search \
  -H "X-API-Key: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"example title","limit":5}'
curl -H "X-API-Key: $TOKEN" 'http://127.0.0.1:8000/v1/jobs?status=queued&limit=50'
curl -X POST -H "X-API-Key: $TOKEN" http://127.0.0.1:8000/v1/jobs/<job-id>/cancel
```
