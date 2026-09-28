# HTTP API reference

Base URL: `http://127.0.0.1:8000`. Interactive spec: `/docs`.

Every endpoint except `GET /healthz` and the signed file endpoint requires an API key:

```http
X-API-Key: <ANNAS_API_TOKEN>
```

`ANNAS_API_TOKEN` is one such key. Per-user keys are issued from the web console
and behave identically — except that a regular user's key only reaches that
user's own jobs, while `ANNAS_API_TOKEN` and console administrators see every
job. `GET /v1/jobs`, `GET /v1/jobs/{id}` and `POST /v1/jobs/{id}/cancel` return
`404` for a job the caller does not own, so existence is not revealed.

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

`status_url` and `download_url` are derived from the request's `Host` header
unless `ANNAS_API_PUBLIC_BASE_URL` is set, which takes precedence. Set it when
the service runs behind a reverse proxy that rewrites `Host`, otherwise clients
receive links carrying the proxy's upstream address.

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

## Library

`GET /v1/library`

Returns only the calling account's completed download files that are still
retained. Every response contains fresh signed `download_url` values; the
stored files expire after `ANNAS_API_FILE_RETENTION_HOURS` (24 by default).

```json
{
  "files": [{
    "id": "<job-id>",
    "name": "Example book",
    "completed_at": 1790000000,
    "available_until": 1790086400,
    "download_url": "https://example.com/v1/files/<job-id>?expires=...&signature=..."
  }],
  "retention_seconds": 86400,
  "count": 1
}
```

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
| `429` | The user's quota is exhausted (daily searches/downloads, or too many jobs running). |
| `500` | Unexpected server error. |

## Example

```bash
TOKEN=$ANNAS_API_TOKEN
curl -X POST http://127.0.0.1:8000/v1/search \
  -H "X-API-Key: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"example title","limit":5}'
curl -H "X-API-Key: $TOKEN" 'http://127.0.0.1:8000/v1/jobs?status=queued&limit=50'
curl -X POST -H "X-API-Key: $TOKEN" http://127.0.0.1:8000/v1/jobs/<job-id>/cancel
```

## Web console endpoints

The console is a static page served at `/`, backed by these JSON endpoints.
Authentication is a `annas_api_session` cookie (HttpOnly, SameSite=Lax), set by
login and cleared by logout; none of them accept `X-API-Key`.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/web/settings` | none | `{setup_required, registration_open, min_password_length}`. |
| `POST` | `/web/register` | none, gated by `registration_open` | Create an account and log in. The first account of an empty database becomes the administrator. |
| `POST` | `/web/login` | none | Log in, setting the session cookie. |
| `POST` | `/web/logout` | session | Delete the session. |
| `GET` | `/web/me` | session | Current account with quota and today's usage. |
| `GET` | `/web/keys` | session | List own keys (prefix, name, timestamps). |
| `POST` | `/web/keys` | session | Create a key; the secret is returned **once**. |
| `POST` | `/web/keys/{id}/revoke` | session | Deactivate one of your keys. |
| `GET` | `/web/usage` | session | `{quota, usage}` for the caller. |
| `GET` | `/web/admin/settings` | admin | Registration toggle and user count. |
| `PATCH` | `/web/admin/settings` | admin | `{registration_open}`. |
| `GET` | `/web/admin/users` | admin | Users with quota and today's usage, paginated. |
| `POST` | `/web/admin/users` | admin | Create a user. |
| `PATCH` | `/web/admin/users/{id}` | admin | `{is_active?, is_admin?, password?}`. |
| `PUT` | `/web/admin/users/{id}/quota` | admin | `{daily_searches, daily_downloads, max_concurrent_jobs}`; `0` is unlimited. |
| `POST` | `/web/admin/users/{id}/usage/reset` | admin | Zero today's counters. |
| `DELETE` | `/web/admin/users/{id}` | admin | Delete a user; keys and quota cascade, job history is kept. |

Job submission and status polling from the console use the same `/v1/*`
endpoints documented above, with the session cookie instead of a key.
