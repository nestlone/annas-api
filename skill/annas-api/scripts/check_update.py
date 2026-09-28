"""Detect whether a newer release of this skill is available.

The skill version lives in the ``VERSION`` file next to ``SKILL.md``. Published
releases are tagged ``skill-v<version>`` and carry the skill zip, its
``.sha256``, and a ``latest.json`` manifest.

Three sources are supported:

``auto`` (default)
    Read ``VERSION`` from the repository's default branch over the raw CDN,
    which has no API rate limit, then try to enrich the report with release
    asset URLs from the GitHub API. Enrichment failures are ignored.
``api``
    Query the GitHub Releases API directly. Unauthenticated callers share a
    limit of 60 requests per hour per IP, so set ``GITHUB_TOKEN`` in build or
    CI environments.
``manifest``
    Read a published ``latest.json`` — handy when the deployment serves one.

Usage::

    python check_update.py                       # JSON report
    python check_update.py --exit-code            # exit 3 when an update exists
    python check_update.py --source api --token $GITHUB_TOKEN
    python check_update.py --manifest-url https://annas.nestlone.com/skill/latest.json

Exit codes: ``0`` check succeeded (regardless of whether an update exists),
``3`` update available together with ``--exit-code``, ``4`` the check failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SKILL_NAME = "annas-api"
DEFAULT_REPO = "nestlone/annas-api"
TAG_PREFIX = "skill-v"
USER_AGENT = "annas-api-skill-update-check/1.0"
RELEASES_API = "https://api.github.com/repos/{repo}/releases?per_page=100"
RAW_VERSION_URL = "https://raw.githubusercontent.com/{repo}/main/skill/annas-api/VERSION"


class UpdateCheckError(Exception):
    """Raised when the local manifest or the remote version source is unusable."""


def skill_root():
    """Directory containing ``SKILL.md`` — ``scripts/`` lives directly below it."""
    return Path(__file__).resolve().parent.parent


def read_local_version(root=None):
    version_file = Path(root) / "VERSION" if root else skill_root() / "VERSION"
    try:
        version = version_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise UpdateCheckError("cannot read {}: {}".format(version_file, exc)) from exc
    if not version:
        raise UpdateCheckError("{} is empty".format(version_file))
    return version


def parse_version(text):
    """Return ``(release_tuple, prerelease_tuple)`` for semver-ish comparisons."""
    cleaned = text.strip().lstrip("vV")
    core, _, prerelease = cleaned.partition("-")
    core_parts = tuple(int(part) for part in core.split(".") if part.isdigit())
    if not core_parts:
        raise UpdateCheckError("unparsable version {!r}".format(text))
    pre_parts = tuple(
        int(part) if part.isdigit() else part for part in prerelease.split(".") if part
    )
    return core_parts, pre_parts


def compare_versions(left, right):
    """``-1`` if left < right, ``0`` if equal, ``1`` if left > right."""
    left_core, left_pre = parse_version(left)
    right_core, right_pre = parse_version(right)
    if left_core != right_core:
        return -1 if left_core < right_core else 1
    if left_pre == right_pre:
        return 0
    if not left_pre:            # a release outranks its prereleases
        return 1
    if not right_pre:
        return -1
    return -1 if left_pre < right_pre else 1


def _open(url, timeout, token=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer {}".format(token)
    try:
        return urlopen(Request(url, headers=headers), timeout=timeout)
    except HTTPError as exc:
        if exc.code in (403, 429):
            detail = exc.read().decode("utf-8", "replace")
            if "rate limit" in detail.lower():
                raise UpdateCheckError(
                    "GET {} hit the GitHub API rate limit; set GITHUB_TOKEN "
                    "or use the default raw source".format(url)) from exc
        raise UpdateCheckError("GET {} failed: HTTP {}".format(url, exc.code)) from exc
    except URLError as exc:
        raise UpdateCheckError("GET {} failed: {}".format(url, exc.reason)) from exc


def fetch_json(url, timeout=20, token=None):
    with _open(url, timeout, token) as response:
        try:
            return json.loads(response.read().decode("utf-8"))
        except ValueError as exc:
            raise UpdateCheckError("GET {} returned invalid JSON".format(url)) from exc


def fetch_text(url, timeout=20, token=None):
    with _open(url, timeout, token) as response:
        return response.read().decode("utf-8")


def _asset_map(release):
    return {
        asset.get("name"): asset.get("browser_download_url")
        for asset in release.get("assets", [])
    }


def candidate_tags(releases):
    """Yield ``(version, release)`` for well-formed skill tags."""
    for release in releases:
        tag = release.get("tag_name") or ""
        if not tag.startswith(TAG_PREFIX):
            continue
        version = tag[len(TAG_PREFIX):]
        try:
            parse_version(version)
        except UpdateCheckError:
            continue
        yield version, release


def newest_release(releases):
    newest = None
    for version, release in candidate_tags(releases):
        if newest is None or compare_versions(version, newest[0]) > 0:
            newest = (version, release)
    return newest


def _release_details_from_api(repo, token, timeout):
    releases = fetch_json(RELEASES_API.format(repo=repo), timeout=timeout, token=token)
    if not isinstance(releases, list):
        raise UpdateCheckError("unexpected release payload from GitHub")
    found = newest_release(releases)
    if found is None:
        raise UpdateCheckError("no {}{{version}} release found in {}".format(TAG_PREFIX, repo))
    version, release = found
    assets = _asset_map(release)
    return {
        "latest": version,
        "tag": release.get("tag_name"),
        "release_url": release.get("html_url"),
        "released_at": release.get("published_at"),
        "zip_url": next((url for name, url in assets.items()
                         if name and name.endswith(".zip")), None),
        "sha256_url": next((url for name, url in assets.items()
                            if name and name.endswith(".sha256")), None),
    }


def _details_from_manifest(manifest_url, token, timeout):
    manifest = fetch_json(manifest_url, timeout=timeout, token=token)
    latest = str(manifest.get("version") or "").strip()
    if not latest:
        raise UpdateCheckError("manifest {} has no version".format(manifest_url))
    assets = manifest.get("assets") or {}
    return {
        "latest": latest,
        "tag": manifest.get("tag"),
        "release_url": manifest.get("release_url"),
        "released_at": manifest.get("released_at"),
        "zip_url": manifest.get("zip_url") or assets.get("zip"),
        "sha256_url": manifest.get("sha256_url") or assets.get("sha256"),
        "sha256": manifest.get("sha256"),
    }


def _details_from_raw(version_url, repo, token, timeout):
    latest = fetch_text(version_url, timeout=timeout, token=token).strip()
    if not latest:
        raise UpdateCheckError("{} is empty".format(version_url))
    parse_version(latest)               # reject a malformed remote value early
    tag = TAG_PREFIX + latest
    return {
        "latest": latest,
        "tag": tag,
        "release_url": "https://github.com/{}/releases/tag/{}".format(repo, tag),
        "released_at": None,
        "zip_url": None,
        "sha256_url": None,
    }


def check_for_update(repo=DEFAULT_REPO, root=None, manifest_url=None, source="auto",
                     token=None, timeout=20, version_url=None):
    """Compare the local skill version with the newest published release."""
    current = read_local_version(root)
    token = token or os.environ.get("GITHUB_TOKEN")

    if manifest_url:
        details = _details_from_manifest(manifest_url, token, timeout)
        resolved = "manifest"
    elif source == "manifest":
        raise UpdateCheckError("--source manifest needs --manifest-url")
    elif source == "api":
        details = _release_details_from_api(repo, token, timeout)
        resolved = "api"
    else:
        raw_url = version_url or RAW_VERSION_URL.format(repo=repo)
        details = _details_from_raw(raw_url, repo, token, timeout)
        resolved = "version"
        # Asset URLs are a convenience, not the answer; never fail the check for them.
        try:
            details.update(_release_details_from_api(repo, token, timeout))
        except UpdateCheckError:
            pass

    report = {
        "name": SKILL_NAME,
        "repo": repo,
        "current": current,
        "latest": details["latest"],
        "update_available": compare_versions(details["latest"], current) > 0,
        "source": resolved,
        "tag": details["tag"],
        "release_url": details["release_url"],
        "zip_url": details["zip_url"],
        "sha256_url": details["sha256_url"],
        "released_at": details["released_at"],
    }
    if details.get("sha256"):
        report["sha256"] = details["sha256"]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check whether a newer skill release exists.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name (default: %(default)s)")
    parser.add_argument("--source", default="auto", choices=["auto", "api", "manifest"],
                        help="where to read the newest version from (default: %(default)s)")
    parser.add_argument("--manifest-url", default=None,
                        help="read a published latest.json (implies --source manifest)")
    parser.add_argument("--version-url", default=None,
                        help="override the raw VERSION endpoint used by --source auto")
    parser.add_argument("--token", default=None,
                        help="GitHub token for the API source (default: GITHUB_TOKEN)")
    parser.add_argument("--version-file", default=None, help="override the local VERSION path")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--exit-code", action="store_true",
                        help="exit 3 when an update is available")
    parser.add_argument("--quiet", action="store_true", help="print nothing, only set the code")
    args = parser.parse_args(argv)

    root = Path(args.version_file).resolve().parent if args.version_file else None
    try:
        report = check_for_update(repo=args.repo, root=root, manifest_url=args.manifest_url,
                                  source=args.source, token=args.token, timeout=args.timeout,
                                  version_url=args.version_url)
    except UpdateCheckError as exc:
        print("update check failed: {}".format(exc), file=sys.stderr)
        return 4

    if not args.quiet:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")

    if report["update_available"] and args.exit_code:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
