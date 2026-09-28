# Changelog

All notable changes to the `annas-api` skill are recorded here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/nestlone/annas-api/compare/skill-v1.0.0...HEAD
[1.0.0]: https://github.com/nestlone/annas-api/releases/tag/skill-v1.0.0
