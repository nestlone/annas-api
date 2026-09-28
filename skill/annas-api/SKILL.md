---
name: annas-api
description: >-
  Retrieve and verify documents through an annas-api deployment. Submit
  asynchronous catalog searches and downloads (by MD5 or direct URL), poll job
  status, inspect or cancel the queue, and fetch validated files through
  short-lived signed URLs. Use when the user wants to search a book catalog,
  download a title, check on or cancel a queued/running job, list jobs, or
  retrieve a previously completed download from an annas-api service. The
  default deployment is https://annas.nestlone.com.
license: MIT
metadata:
  version: "1.0.1"
  service: annas.nestlone.com
  api-contract: v1
---

# annas-api

`annas-api` exposes a small asynchronous HTTP API. You submit a job, get a job
ID back immediately, poll until the job reaches a terminal state, and — for
downloads — fetch the file from a time-limited signed URL. Search is catalog
metadata only; downloads are validated (length, MD5, PDF readability) before
they are published.

## Configuration

| Setting | Environment variable | Default |
| --- | --- | --- |
| Service root | `ANNAS_API_BASE_URL` | `https://annas.nestlone.com` |
| API key | `ANNAS_API_TOKEN` | — (required) |

The API key is sent as the `X-API-Key` header on every request except
`GET /healthz` and the signed file route. Never print the key or a signed
download URL into shared logs — both are credentials.

## Quick start

The scripts under `scripts/` use only the Python standard library, so they run
in a bare sandbox with no install step.

```bash
export ANNAS_API_TOKEN=...                       # required
BASE=https://annas.nestlone.com                  # default; override for testing

# 1. search and wait for the result set
python scripts/annas_cli.py search "The Great Gatsby" --ext epub --limit 5 --wait

# 2. download one hit by its MD5, waiting and saving the validated file
python scripts/annas_cli.py download --md5 <32-hex-digest> --wait --save ./books

# 3. inspect the queue and cancel work that has not started yet
python scripts/annas_cli.py jobs --status queued
python scripts/annas_cli.py cancel <job-id>
```

Library use:

```python
from annas_client import AnnasClient

client = AnnasClient()                           # domain + ANNAS_API_TOKEN
job = client.run_search("The Great Gatsby", ext="epub", limit=5)
path = client.run_search_to_file("The Great Gatsby", "./books", ext="epub")
```

## Job model

```
queued ──► running ──► completed
   │            └────► failed
   └─────────────────► cancelled
```

- `POST /v1/search` and `POST /v1/downloads` return `202` with a job ID.
- Poll `GET /v1/jobs/{id}`; terminal states are `completed`, `failed`,
  `cancelled`.
- Only a `queued` job can be cancelled. A `running` job is never interrupted,
  and cancelling anything else returns `409`.
- A completed search puts an array of hits in `result`. A completed download
  adds `download_url` — a signed link that needs no API key.
- Finished jobs and their files are deleted after the deployment's retention
  window (24 hours by default). Fetch a result before it expires; an expired
  job returns `404`.

## Endpoint summary

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Liveness probe (no auth). |
| `POST` | `/v1/search` | Enqueue a catalog search. |
| `POST` | `/v1/downloads` | Enqueue a download by `md5` or `direct_url`. |
| `GET` | `/v1/jobs` | List job summaries, newest first. |
| `GET` | `/v1/jobs/{id}` | Fetch one job. |
| `POST` | `/v1/jobs/{id}/cancel` | Cancel a **queued** job. |
| `GET` | `/v1/files/{id}` | Signed file delivery (no auth; `expires` + `signature`). |

Full field rules, status codes, and response shapes: [reference.md](reference.md).
Runnable end-to-end recipes: [examples.md](examples.md).

## Operating rules

1. **One source per download.** `POST /v1/downloads` takes exactly one of
   `md5` or `direct_url`. Prefer `md5` when the hit came from a search.
2. **Poll with a budget.** Use `--wait`/`wait()` rather than a fixed `sleep`;
   searches finish in seconds, downloads can take minutes. If a job is still
   `running` when the budget runs out, report that rather than retrying blindly.
3. **Check the terminal status.** A `completed` job with an empty `result`
   means the catalog had no match — that is not an error. Report `failed` jobs
   with the `error` string.
4. **Treat signed URLs as secrets.** They are bearer credentials valid for the
   configured TTL (15 minutes by default). Do not paste them into public issues
   or logs; save the bytes to disk instead.
5. **Do not retry `404`.** It means the job or file is gone (never existed or
   already purged by retention).
6. **Cancel is best-effort by design.** A `409` on cancel means the job had
   already started or finished — report it, do not escalate.

## Updating the skill

Releases are tagged `skill-v<version>` and carry the zip, its SHA-256, and a
`latest.json` manifest. The installed copy records its version in `VERSION`, so
updates can be detected mechanically:

```bash
python scripts/check_update.py               # JSON: current vs. latest
python scripts/check_update.py --exit-code   # exit 3 when an update exists
```

`update_available` is the answer; `current` and `latest` explain it. The default
source reads `VERSION` from the repository's default branch over the raw CDN and
then optionally enriches the report with release asset URLs — the raw read has
no API rate limit, so the check keeps working when the GitHub API does not. Use
`--source api --token "$GITHUB_TOKEN"` to force the Releases API, or
`--manifest-url` to read a manifest the deployment serves.

Detect an update, then install the published archive; see
[../README.md](../README.md) for the download and checksum procedure.
