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
- **One-command deploy** — Docker Compose; secrets live in an untracked `.env`.

## Quick start

Requires Docker with the Compose plugin.

```bash
cp .env.example .env   # set FERRY_API_TOKEN and FERRY_API_SIGNING_KEY
docker compose up --build -d
curl http://127.0.0.1:8000/healthz
```

The service binds to `127.0.0.1:8000` only. Interactive OpenAPI docs:
<http://127.0.0.1:8000/docs>.

## Usage

```bash
# Submit a search.
curl -X POST http://127.0.0.1:8000/v1/search \
  -H "X-API-Key: $FERRY_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"example title","limit":5}'

# Poll a job, inspect the queue, cancel a queued job.
curl -H "X-API-Key: $FERRY_API_TOKEN" http://127.0.0.1:8000/v1/jobs/<job-id>
curl -H "X-API-Key: $FERRY_API_TOKEN" 'http://127.0.0.1:8000/v1/jobs?status=queued'
curl -X POST -H "X-API-Key: $FERRY_API_TOKEN" \
  http://127.0.0.1:8000/v1/jobs/<job-id>/cancel
```

See the [HTTP API reference](docs/api.md) for the full contract and the
[CLI reference](docs/cli.md) for local use.

## Configuration

The service reads a small set of environment variables (see
[`.env.example`](.env.example)):

| Variable | Default | Purpose |
| --- | --- | --- |
| `FERRY_API_TOKEN` | — | `X-API-Key` for management endpoints (required). |
| `FERRY_API_SIGNING_KEY` | — | HMAC key for download URLs (required; must differ from the token). |
| `FERRY_API_WORKERS` | `2` | Local worker count, clamped to 1–10. |
| `FERRY_API_FILE_URL_TTL` | `900` | Download-link lifetime in seconds (60–86400). |
| `FERRY_PROXY_POOL_URL` | — | Optional rotating proxy pool for CDN downloads. |

Full details: [Configuration](docs/configuration.md).

## Documentation

- [Architecture](docs/architecture.md)
- [HTTP API](docs/api.md)
- [CLI](docs/cli.md)
- [Configuration](docs/configuration.md)
- [Development](docs/development.md)
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Code of Conduct](CODE_OF_CONDUCT.md)

## License

[MIT](LICENSE)

---

[中文说明](README.zh-CN.md)
