# Architecture

annas-api separates fast request handling from slow network work. The API layer
authenticates, validates, and enqueues; worker threads perform the search or
download; state lives in SQLite and finished files in a data volume.

```mermaid
flowchart LR
  Client[API caller] --> API[FastAPI]
  Console[Web console] --> API
  API --> Accounts[(users, keys, quotas, sessions)]
  Accounts --> DB[(SQLite jobs)]
  API --> DB
  API --> Queue[Worker pool]
  Queue --> Core[Search & download]
  Core --> Files[/data/downloads]
  Files --> Signed[Signed URL]
  Signed --> Client
  DB --> API
```

## Components

| Module | Responsibility |
| --- | --- |
| `annas_api.api` | Routes, caller identity, download-URL signing, console mounting. |
| `annas_api.web` | Console endpoints: sessions, self-service keys, administrator routes. |
| `annas_api.accounts` | Users, API keys, sessions, quotas and usage counters; the quota gate. |
| `annas_api.security` | Password hashing and API-key/session secret generation (stdlib only). |
| `annas_api.db` | SQLite connection settings (WAL, busy timeout, foreign keys) and schema migration. |
| `annas_api.jobs` | SQLite job store, worker pool, restart semantics, per-job directories. |
| `annas_api.engine` | CLI, search, direct-URL resolution, download orchestration, optional conversion. |
| `annas_api.downloader` | HTTPS/public-address validation, redirects, resume, hashing, PDF checks. |
| `annas_api.proxy_pool` | Rotating proxy acquisition for CDN downloads. |
| `annas_api/static` | The console page (HTML/CSS/JS), shipped as package data. |
| `compose.yaml` | Single-container deployment, volume mounts, runtime config. |

## Accounts and ownership

Accounts, keys, quotas and sessions share the `jobs.sqlite3` file, so the quota
check and the job insert happen in one transaction: a submit takes
`BEGIN IMMEDIATE`, reserves its unit, and only then inserts the row. Two
concurrent submits therefore cannot both slip past the same limit.

A job records an `owner_id`. Administrators and `FERRY_API_TOKEN` see every job;
a regular key sees only its owner's, and other jobs answer `404`. Jobs created
before accounts existed have no owner, which reads as "visible to administrators
and to the anonymous caller of an open deployment".

## Job lifecycle

`queued` → `running` → `completed` | `failed` | `cancelled`

- Only `queued` jobs can be cancelled (to `cancelled`); a `running` job cannot be
  interrupted.
- A completed search stores its structured result; a download records its final
  file path only after validation.
- Failures store a truncated error summary; partial transfers may be kept for a retry.
- On restart, unfinished jobs are marked failed rather than falsely reported as complete.
- Terminal jobs expire after `FERRY_API_FILE_RETENTION_HOURS`: a sweeper deletes the
  job directory and then the database row. Active jobs are never purged.

## Data boundaries

- Callers receive job IDs, result metadata, state, and signed file URLs only.
- Upstream CDN addresses are never exposed in responses.
- Each download runs in `/data/downloads/<job-id>/`; delivery re-checks that the
  file is still inside that directory.

## Scaling

The current design targets a single node and a controlled workload. One instance
runs up to 10 concurrent jobs, all sharing the configured network egress. To scale
horizontally, keep the API contract and replace the backing pieces:

- SQLite → PostgreSQL.
- Worker pool → Redis/Celery, RQ, or another persistent queue.
- Docker volume → S3/R2/MinIO signed URLs.

Do not raise concurrency to bypass a target service's limits; the ceiling should
follow your authorization, egress bandwidth, CPU, disk, and the service's policy.
