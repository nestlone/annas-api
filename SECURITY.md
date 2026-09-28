# Security Policy

## Scope

Security issues include API authentication, signed download links, path handling,
download-URL validation, redirects, job isolation, dependencies, and container
configuration.

## Reporting

Please use the hosting platform's private vulnerability reporting first. If that is
unavailable, open a non-technical issue asking for a private channel.

Do not publish exploitation steps, API tokens, signed URLs, session data,
credentials, or private files. Maintainers will acknowledge the report, assess
impact, and coordinate a fix and disclosure.

## Deployer responsibilities

- Treat `.env` and the Docker volume as sensitive assets.
- Put production behind a TLS reverse proxy and restrict network ingress.
- Define policy for token rotation, log redaction, retention, and backups.
- Admit only authorized users and content.
