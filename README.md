# annas-api

[![CI](https://github.com/nestlone/annas-api/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nestlone/annas-api/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![Docker](https://img.shields.io/badge/deploy-docker-blue.svg)](compose.yaml)

Asynchronous HTTP API and CLI for verified document retrieval: search a book
index, download files with integrity checks, and optionally convert DjVu to PDF.

> This is not an official client of any third-party website. Use it only for
> material you are authorized to access, download, and redistribute. Deployers are
> responsible for complying with applicable law, terms of service, and network policy.

## Features

- **Async jobs** — submit a search or download, get a job ID immediately, poll for the result.
- **Durable state** — job status and results persist in SQLite across restarts.
- **Verified downloads** — length, MD5 (when known), and PDF readability are checked before a file is published.
- **Signed delivery** — files are served through short-lived HMAC URLs; the upstream CDN address is never exposed.
- **Resumable transfers** — interrupted downloads resume with byte-range requests.
- **Proxy routing** — optionally route CDN downloads through a rotating proxy pool.
- **Bounded concurrency** — a local worker pool (1–10) with FIFO queueing.
- **Web console** — a built-in page for per-user API keys, online search and download, quotas, and an administrator view for registration and users.
- **One-command deploy** — Docker Compose; secrets live in an untracked `.env`.

## Quick start

Requires Docker with the Compose plugin.

> **Naming reset:** configuration now uses `ANNAS_API_*` variables, the Compose
> service is `annas-api`, and issued API keys begin with `annas_`. Copy the
> current `.env.example` when creating a deployment; earlier variable names are
> intentionally unsupported.

```bash
cp .env.example .env   # set ANNAS_API_TOKEN, ANNAS_API_SIGNING_KEY and ANNAS_API_ADMIN_PASSWORD
docker compose up --build -d
curl http://127.0.0.1:8000/healthz
```

Then open `http://127.0.0.1:8000/` for the web console and log in with
`ANNAS_API_ADMIN_USERNAME` / `ANNAS_API_ADMIN_PASSWORD` — see [Web console](docs/web.md).

The service binds to `127.0.0.1:8000` only. Interactive OpenAPI docs:
<http://127.0.0.1:8000/docs>.

### Production updates

`compose.yaml` mounts `annas_api/` read-only by default, so application code
can be updated quickly on the same host with `git pull`:

```bash
docker compose up --build -d  # first run
git pull --ff-only
docker compose restart annas-api
curl http://127.0.0.1:8000/healthz
```

Data remains in the `annas-api-data` volume. Changes to `pyproject.toml`,
dependencies, or the `Dockerfile` still require `--build`. Do not use
`--reload` in production.

## Usage

```bash
# Submit a search.
curl -X POST http://127.0.0.1:8000/v1/search \
  -H "X-API-Key: $ANNAS_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"example title","limit":5}'

# Poll a job, inspect the queue, cancel a queued job.
curl -H "X-API-Key: $ANNAS_API_TOKEN" http://127.0.0.1:8000/v1/jobs/<job-id>
curl -H "X-API-Key: $ANNAS_API_TOKEN" 'http://127.0.0.1:8000/v1/jobs?status=queued'
curl -X POST -H "X-API-Key: $ANNAS_API_TOKEN" \
  http://127.0.0.1:8000/v1/jobs/<job-id>/cancel
```

See the [HTTP API reference](docs/api.md) for the full contract and the
[CLI reference](docs/cli.md) for local use.

## Configuration

The service reads a small set of environment variables (see
[`.env.example`](.env.example)):

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANNAS_API_TOKEN` | — | `X-API-Key` for management endpoints (required). |
| `ANNAS_API_SIGNING_KEY` | — | HMAC key for download URLs (required; must differ from the token). |
| `ANNAS_API_WORKERS` | `2` | Local worker count, clamped to 1–10. |
| `ANNAS_API_FILE_URL_TTL` | `900` | Download-link lifetime in seconds (60–86400). |
| `ANNAS_API_FILE_RETENTION_HOURS` | `24` | Hours a finished job and its file survive (1–8760). |
| `ANNAS_API_PUBLIC_BASE_URL` | — | Base URL for `status_url`/`download_url`; set it behind a reverse proxy that rewrites `Host`. |
| `ANNAS_API_ADMIN_USERNAME` | `admin` | Console administrator, created on first start. |
| `ANNAS_API_ADMIN_PASSWORD` | — | Initial password for that administrator; without it the first registrant becomes the administrator. |
| `ANNAS_API_REGISTRATION_OPEN` | `0` | Seeds whether the console offers open registration. |
| `ANNAS_API_SESSION_TTL_HOURS` | `168` | Console session lifetime. |
| `ANNAS_API_PROXY_POOL_URL` | — | Optional rotating proxy pool for CDN downloads. |

Full details: [Configuration](docs/configuration.md).

## Documentation

- [Architecture](docs/architecture.md)
- [HTTP API](docs/api.md)
- [CLI](docs/cli.md)
- [Configuration](docs/configuration.md)
- [Development](docs/development.md)
- [Agent skill](skill/README.md) — packaged client for agent integration
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Code of Conduct](CODE_OF_CONDUCT.md)

## License

[MIT](LICENSE)

---

[中文说明](README.zh-CN.md)
