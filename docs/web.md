# Web console

The service serves a console at the site root (`/`). It offers per-user API
keys, online search and download, a square-edged cel-shaded interface, and —
for administrators — registration control, user management and quotas.
Everything is stored in the same SQLite database as the jobs.

## First start

Set an administrator in the environment before the first deploy:

```ini
ANNAS_API_ADMIN_USERNAME=admin
ANNAS_API_ADMIN_PASSWORD=change-me-to-something-strong
```

The account is created on start. The variable only *seeds* it, so a password
changed from the console is not reverted by a later restart.

If `ANNAS_API_ADMIN_PASSWORD` is empty, no administrator is created and the **first
account to register becomes the administrator**. That path exists so a fresh
deployment can be set up without shell access; on a public host it also means
whoever registers first wins, so set the password instead.

## Registration

Registration is closed by default and is controlled by the administrator from
the console. `ANNAS_API_REGISTRATION_OPEN=1` seeds it to open on first start; the
console value wins afterwards. A registrant is always a regular user.

## Using the console

| Screen | What it does |
| --- | --- |
| **检索 (Search)** | Enter a query, optionally a format and a result count. Each hit has a Download button that queues the file and links it when ready. |
| **图书馆 (Library)** | Your verified completed downloads. Files remain available for `ANNAS_API_FILE_RETENTION_HOURS` (24 by default); opening the shelf issues a fresh short-lived download link. |
| **我的任务 (My jobs)** | Recent jobs with status; queued jobs can be cancelled, completed downloads can be saved. |
| **API 密钥 (API keys)** | Create, rename and revoke keys. The secret is displayed **once** at creation — only its SHA-256 is stored, so it cannot be recovered later. |
| **我的额度 (My quota)** | Today's search and download counts against the limits, plus jobs currently running. |
| **账户 (Account)** | Change your password. This signs every logged-in device out. |
| **管理后台 (Admin)** | Registration toggle, user creation, account and role editing, password reset, quotas and usage controls. |

## API keys

A key replaces `ANNAS_API_TOKEN` in any request:

```bash
curl -H "X-API-Key: annas_..." https://example.com/v1/jobs
```

Keys carry their owner's identity. A regular user sees only their own jobs;
`ANNAS_API_TOKEN` and administrators see all of them. Revoking a key, or
disabling its owner, takes effect on the next request.

Key remarks are editable and never change the secret. To rotate a secret,
create a new key, update its client, and then revoke the old one.

## Quotas

Quotas are set per user in the admin table:

| Field | Meaning |
| --- | --- |
| 检索额度 (`daily_searches`) | Searches allowed per UTC day. |
| 下载额度 (`daily_downloads`) | Downloads allowed per UTC day. |
| 并发 (`max_concurrent_jobs`) | Jobs allowed to be queued or running at once. |

The two counters are independent and reset at UTC midnight. **`0` means
unlimited.** A submit that would exceed a limit is refused with HTTP `429` and a
message naming which limit was hit; it is not counted. A job that fails still
consumes its unit — an administrator can zero the counters with
**重置用量 (Reset usage)**.

`ANNAS_API_TOKEN` is an administrator key and is never quota-limited.

## Sessions

Logging in sets an HttpOnly, SameSite=Lax cookie valid for
`ANNAS_API_SESSION_TTL_HOURS` (168 by default). The cookie is marked `Secure` when
the public base URL is HTTPS. Sessions are rows in the database, so logging out,
disabling a user, or deleting a user invalidates them immediately.

Because authentication is cookie-based, the console is served from the same
origin as the API it calls. Pointing a different domain at the same container
requires that domain to be listed on the reverse proxy.

Users can change their own password after confirming the current password.
Administrators can reset another user's password. Either operation invalidates
that user's browser sessions, so the user must sign in again. The console also
prevents administrators from removing the last enabled administrator role.

## Security notes

- Passwords are hashed with `scrypt` (via `hashlib`), falling back to `pbkdf2`.
- Only key hashes are stored; a lost key is reissued, never recovered.
- `SameSite=Lax` blocks cross-site state-changing requests. Switching to
  `SameSite=None` would require adding CSRF tokens.
- Put the console behind HTTPS — the session cookie, and the API keys typed into
  it, otherwise travel in clear text.
