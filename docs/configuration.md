# Configuration

## Service (Docker)

```bash
cp .env.example .env
```

`.env` is git-ignored; keep it in a protected location.

```ini
ANNAS_API_TOKEN=replace-with-a-long-random-api-token
ANNAS_API_SIGNING_KEY=replace-with-a-different-long-random-signing-key
ANNAS_API_WORKERS=8
ANNAS_API_FILE_URL_TTL=900
ANNAS_API_FILE_RETENTION_HOURS=24
ANNAS_API_PUBLIC_BASE_URL=https://annas.nestlone.com
ANNAS_API_ADMIN_USERNAME=admin
ANNAS_API_ADMIN_PASSWORD=replace-with-a-strong-password
ANNAS_API_REGISTRATION_OPEN=0
ANNAS_API_SESSION_TTL_HOURS=168
```

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `ANNAS_API_TOKEN` | yes | — | `X-API-Key` for management endpoints. |
| `ANNAS_API_SIGNING_KEY` | yes | — | HMAC key for download URLs; must differ from the token. |
| `ANNAS_API_WORKERS` | no | `2` | Local worker count, clamped to 1–10. |
| `ANNAS_API_FILE_URL_TTL` | no | `900` | Download-link lifetime, clamped to 60–86400 seconds. |
| `ANNAS_API_FILE_RETENTION_HOURS` | no | `24` | Hours a finished job and its file survive, clamped to 1–8760. |
| `ANNAS_API_DATA_DIR` | no | `/data` | Job database and delivered files (container path). |
| `ANNAS_API_PUBLIC_BASE_URL` | no | — | Base URL for the `status_url` and `download_url` links. Set it when the service sits behind a reverse proxy that does not forward the original `Host` header; unset, links are derived from the request. |
| `ANNAS_API_ADMIN_USERNAME` | no | `admin` | Console administrator, created on first start. |
| `ANNAS_API_ADMIN_PASSWORD` | no | — | Initial password for that administrator. Only *seeds* the account: a password changed in the console is not reverted on restart. Without it, the first account to register becomes the administrator. |
| `ANNAS_API_REGISTRATION_OPEN` | no | `0` | Seeds whether the console offers open registration. The administrator owns the setting afterwards. |
| `ANNAS_API_SESSION_TTL_HOURS` | no | `168` | Console session lifetime, clamped to 1–8760. |
| `ANNAS_API_TOKEN_USERNAME` | no | `api-token` | Account that `ANNAS_API_TOKEN` is registered under. |
| `ANNAS_API_SESSION_SECURE` | no | HTTPS base URL | Force the `Secure` flag on the session cookie. |
| `ANNAS_API_PROXY_POOL_URL` | no | — | Rotating proxy-pool endpoint for CDN downloads. |

Each worker starts its own browser process, so memory grows with the worker count;
keep it at or below 8 on a 2 GB host. Recreate the container after changes:
`docker compose up -d`.

## Build download sources

The following `.env` variables apply while building the image rather than while
the service is running:

| Variable | Default | Description |
| --- | --- | --- |
| `ANNAS_API_PIP_INDEX_URL` | Tsinghua PyPI mirror | Python package index used by `pip`. |
| `ANNAS_API_DEBIAN_MIRROR_URL` | Tsinghua Debian mirror | Main Debian package source used for Playwright system dependencies. Debian security updates remain official. |
| `ANNAS_API_PLAYWRIGHT_DOWNLOAD_HOST` | empty | Optional trusted Playwright browser-artifact repository. Empty uses the official CDN. |
| `ANNAS_API_PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT` | `120000` | Browser-download connection timeout in milliseconds. |

Changing any of these values requires `docker compose up --build -d`.
Docker Hub registry mirrors are configured in the host Docker daemon rather than
in this project; use only an organization-approved registry mirror.

## Accounts and the web console

The service serves a console at `/` with per-user API keys, online search and
download, and an administrator view for registration and quotas. Accounts, keys,
sessions, quotas and usage counters live in the same SQLite file as the jobs
(`/data/jobs.sqlite3`), opened in WAL mode so console writes and worker writes do
not block each other.

`ANNAS_API_TOKEN` is registered as an administrator key on every start, so it
can be used by API clients and the bundled agent skill. Jobs created before
accounts existed have no owner and are visible only to administrators.

Quotas are per user and per UTC day: `daily_searches` and `daily_downloads` are
counted separately, and `max_concurrent_jobs` caps parallel work. Zero means
unlimited, and a submit that would exceed a limit is rejected with `429`.

See [Web console](web.md) for the user and operator guide.

## Container permissions

The image runs as `root`, which owns the app directory, the `/data` volume, and
`/ms-playwright`. For a non-root deployment, adjust the UID/GID of the volume and
browser directory accordingly.

## CLI

The CLI reads optional settings from `~/.annas_api/config.json`, falling back to
built-in defaults:

```json
{
  "proxy": "auto",
  "proxy_bypass_hosts": ["annas-archive.gl"],
  "default_download_dir": "~/Downloads/AnnasAPI",
  "auto_convert_djvu": true,
  "headless": true
}
```

Hosts in `proxy_bypass_hosts` (and their subdomains) are reached directly. This
affects application-level HTTP/SOCKS proxies only; a global VPN or TUN still
intercepts traffic at the OS layer.

### Rotating proxy pool

With `ANNAS_API_PROXY_POOL_URL` set, **CDN downloads** egress through pool IPs. The
browser (search and direct-link scraping) stays direct by default, because pool IPs
are usually rejected by the mirror's DDoS-Guard challenge. The endpoint returns
plain text (`wt=text`, `method=http`) such as `1.2.3.4:8080`, parsed as
`http://1.2.3.4:8080`.

```ini
ANNAS_API_PROXY_POOL_URL=https://api.example.com/ip/get?appKey=KEY&appSecret=SECRET&cnt=&wt=text&method=http
```

- Each acquisition returns a different exit IP; a single download keeps one IP and
  rotates (resuming) on `429`, gateway, or connection errors, up to 3 retries.
- To send the browser through the pool too, set `"proxy_pool_browser": true` in the
  CLI config (default `false`). Do not enable it if the pool fails the challenge.
- The URL carries credentials, so it lives only in `.env`; `save_dynamic_config()`
  never writes it to disk.
- `proxy_pool_scheme` (default `http`) overrides the scheme prefix. Clearing the
  variable returns to direct/auto behaviour.

## Data retention

The `annas-api-data` volume holds:

- `/data/jobs.sqlite3` — job state and error summaries.
- `/data/downloads/` — validated, delivered files.

A background sweeper runs at startup and every 10 minutes. Once a job has been in a
terminal state (`completed`, `failed`, `cancelled`) for longer than
`ANNAS_API_FILE_RETENTION_HOURS`, it deletes the job directory and then the database
row. Queued and running jobs are never touched. After expiry, `GET /v1/jobs/{id}`
returns `404` and the signed download URL stops resolving.

Because the link TTL (default 15 minutes) is far shorter than the retention window,
a file is normally fetched long before cleanup. Set the window longer only if clients
may return to a completed job hours later.

Production deployments should still define quota, backup, and access policies.
`docker compose down -v` deletes the volume and all its data.
