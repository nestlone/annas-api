# Architecture

annas-api separates fast request handling from slow network work. The API layer
authenticates, validates, and enqueues; worker threads perform the search or
download; state lives in SQLite and finished files in a data volume.

```mermaid
flowchart LR
  Client[API caller] --> API[FastAPI]
  API --> DB[(SQLite jobs)]
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
| `annas_api.api` | Routes, API-key authentication, download-URL signing. |
| `annas_api.jobs` | SQLite job store, worker pool, restart semantics, per-job directories. |
| `annas_api.engine` | CLI, search, direct-URL resolution, download orchestration, optional conversion. |
| `annas_api.downloader` | HTTPS/public-address validation, redirects, resume, hashing, PDF checks. |
| `annas_api.proxy_pool` | Rotating proxy acquisition for CDN downloads. |
| `compose.yaml` | Single-container deployment, volume mounts, runtime config. |

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
