# Changelog

All notable changes to the `annas-api` skill are recorded here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.4] - 2026-09-28

### Changed

- `reference.md` records that a deployment may issue per-user API keys from its
  web console. They authenticate identically; the only difference is visibility,
  since a regular user's key reaches that user's own jobs while an administrative
  key sees every job.

## [1.0.3] - 2026-09-28

### Fixed

- `AnnasClient.save()` now reads the RFC 5987 `filename*=utf-8''…` form that the
  service sends, so a saved file keeps its real name instead of falling back to
  `download.bin`. A quoted `filename=` is still honoured.
- A server-supplied filename is reduced to its basename before use, so a
  crafted `Content-Disposition` cannot write outside the target directory.
- `check_update.read_local_version()` accepts a plain string path as well as a
  `Path`.

## [1.0.2] - 2026-09-28

### Fixed

- The client flags (`--base-url`, `--token`, `--token-file`, `--http-timeout`,
  `--pretty`) are now accepted on either side of the subcommand, so
  `annas_cli.py jobs --pretty` no longer fails with an argument error.
- `annas_cli.py download --save` without `--wait` is rejected with a clear
  message instead of silently doing nothing.

### Added

- Offline tests for the CLI, including argument position, token files, exit
  codes, and saving a downloaded file.

## [1.0.1] - 2026-09-28

### Fixed

- The update check no longer depends solely on the GitHub Releases API, which
  allows only 60 unauthenticated requests per hour per IP. The default source
  now reads `VERSION` from the default branch over the raw CDN and only
  enriches the report with release asset URLs when the API is reachable.

### Added

- `check_update.py --source {auto,api,manifest}`, `--token` (or `GITHUB_TOKEN`),
  and `--version-url`, plus a clear message when an API rate limit is hit.

## [1.0.0] - 2026-09-28

### Added

- `SKILL.md` describing the asynchronous job model, operating rules, and
  configuration.
- `reference.md` — complete endpoint, field, state, and error reference for the
  `v1` contract.
- `examples.md` — runnable recipes for search, download, queue inspection,
  cancellation, and error handling.
- `scripts/annas_client.py` — standard-library client (`AnnasClient`,
  `AnnasApiError`) with polling and file-delivery helpers.
- `scripts/annas_cli.py` — JSON-emitting command line over the same contract.
- `scripts/check_update.py` — compares the local `VERSION` with the newest
  `skill-v*` GitHub release and reports whether an update is available.
- `tests/test_client.py` — offline tests backed by a standard-library HTTP stub.

[Unreleased]: https://github.com/nestlone/annas-api/compare/skill-v1.0.4...HEAD
[1.0.4]: https://github.com/nestlone/annas-api/compare/skill-v1.0.3...skill-v1.0.4
[1.0.3]: https://github.com/nestlone/annas-api/compare/skill-v1.0.2...skill-v1.0.3
[1.0.2]: https://github.com/nestlone/annas-api/compare/skill-v1.0.1...skill-v1.0.2
[1.0.1]: https://github.com/nestlone/annas-api/compare/skill-v1.0.0...skill-v1.0.1
[1.0.0]: https://github.com/nestlone/annas-api/releases/tag/skill-v1.0.0
