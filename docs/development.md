# Development

## Setup

Requires Python 3.9+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the API locally with the project `.env`:

```bash
cp .env.example .env
uvicorn --env-file .env annas_api.api:app --reload
```

## Tests

Tests must be offline, deterministic, and free of external-site dependencies.

```bash
python -m unittest discover -s tests -v
python -m compileall -q annas_api tests
git diff --check
```

Coverage includes URL guarding, resume, integrity, JSON output, background jobs,
authentication, accounts and quotas, and per-job directory isolation.

Two conventions matter when adding tests:

- `tests/_fixtures.py` builds a `fake_settings(...)` namespace covering every
  attribute of `Settings`, so a new setting must be added there too.
- A job executes in a worker thread, so `patch("annas_api.jobs.search_books", …)`
  (or `download_book`) must stay active until the queue drains — patching only
  around the HTTP call lets the real scraper run afterwards. `tests/test_quota.py`
  shows the shape.

## Commits

- One focused purpose per commit; imperative, short subject.
- Update docs and tests with any API or CLI change.
- Never commit `.env`, downloads, SQLite databases, tokens, or temporary links.
- Do not make bulk automated requests against real sites as a test strategy.

## Release checklist

1. Run the tests, the compile check, and `docker compose config`.
2. Review dependencies, licenses, and security impact.
3. Bump the version and update the changelog and docs.
4. Verify container startup and `/healthz` in a clean environment.
