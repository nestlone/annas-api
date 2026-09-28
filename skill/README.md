# annas-api skill

An [Agent Skill](https://docs.claude.com/en/docs/claude-code/skills) that lets an
agent use an `annas-api` deployment: submit asynchronous search and download
jobs, poll their status, inspect or cancel the queue, and retrieve validated
files through signed URLs.

The skill targets the service described in the [HTTP API
reference](../docs/api.md). Its default base URL is
`https://annas.nestlone.com` and is overridable through `ANNAS_API_BASE_URL`.

## Layout

```
skill/
├── README.md                 this file — install, releases, verification
└── annas-api/
    ├── SKILL.md              entry point: job model, rules, configuration
    ├── reference.md          full v1 endpoint/field/error reference
    ├── examples.md           runnable end-to-end recipes
    ├── CHANGELOG.md          version history
    ├── VERSION               version source of truth (semver)
    ├── skill.json            machine-readable manifest
    ├── scripts/
    │   ├── annas_client.py   standard-library API client
    │   ├── annas_cli.py      JSON command line
    │   └── check_update.py   release/version checker
    └── tests/
        └── test_client.py    offline tests (stdlib HTTP stub)
```

## Install

Skill releases are tagged `skill-v<version>` (kept separate from the application
releases, so the repository's "latest release" still points at the service).
Download the archive for the version you want and unzip it into your skills
directory — the archive contains a top-level `annas-api/` folder:

```bash
VERSION=1.0.0
BASE=https://github.com/nestlone/annas-api/releases/download/skill-v$VERSION
curl -L -o annas-api-skill.zip "$BASE/annas-api-skill-$VERSION.zip"
curl -L -o annas-api-skill.zip.sha256 "$BASE/annas-api-skill-$VERSION.zip.sha256"
sha256sum -c annas-api-skill.zip.sha256
unzip annas-api-skill.zip -d ~/.claude/skills/
```

Set the API key in the agent's environment before use:

```bash
export ANNAS_API_TOKEN=...
```

## Versioning

The version lives in `annas-api/VERSION` and is mirrored into `SKILL.md`
(`metadata.version`) and `skill.json`. A push that changes the skill publishes a
release tagged `skill-v<version>` with three assets:

| Asset | Purpose |
| --- | --- |
| `annas-api-skill-<version>.zip` | The installable skill. |
| `annas-api-skill-<version>.zip.sha256` | Checksum for verification. |
| `latest.json` | Manifest of the newest version, for update checks. |

Bump `VERSION` (and the mirrors + `CHANGELOG.md`) to publish a new release.

## Check for updates

```bash
python annas-api/scripts/check_update.py               # JSON report
python annas-api/scripts/check_update.py --exit-code    # exit 3 if outdated
```

The report compares the local `VERSION` against the newest `skill-v*` release
and returns `current`, `latest`, `update_available`, and the release asset URLs.
With `--manifest-url`, it reads a published `latest.json` instead of the GitHub
API.

## Development

The scripts depend on nothing outside the Python 3.9+ standard library. Run the
offline tests from the repository root:

```bash
python -m unittest discover -s skill/annas-api/tests -v
```

CI runs the same suite on every push; see
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml).
