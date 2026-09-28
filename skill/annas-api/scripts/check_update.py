"""Detect whether a newer release of this skill is available.

The skill version lives in the ``VERSION`` file next to ``SKILL.md``. Published
releases are tagged ``skill-v<version>`` in the repository and carry three
assets: the skill zip, its ``.sha256``, and a ``latest.json`` manifest. This
script compares the local version against the newest such tag.

Usage::

    python check_update.py                     # human-readable JSON
    python check_update.py --exit-code         # exit 3 when an update exists
    python check_update.py --manifest-url URL  # read a published latest.json

Exit codes: ``0`` up to date (or update found without ``--exit-code``), ``3``
update available with ``--exit-code``, ``4`` the check itself failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SKILL_NAME = "annas-api"
DEFAULT_REPO = "nestlone/annas-api"
TAG_PREFIX = "skill-v"
RELEASES_API = "https://api.github.com/repos/{repo}/releases?per_page=100"
USER_AGENT = "annas-api-skill-update-check/1.0"


class UpdateCheckError(Exception):
    """Raised when the local manifest or the remote release list is unusable."""


def skill_root():
    """Directory containing ``SKILL.md`` — ``scripts/`` lives directly below it."""
    return Path(__file__).resolve().parent.parent


def read_local_version(root=None):
    version_file = (root or skill_root()) / "VERSION"
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


def fetch_json(url, timeout=20):
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise UpdateCheckError("GET {} failed: HTTP {}".format(url, exc.code)) from exc
    except URLError as exc:
        raise UpdateCheckError("GET {} failed: {}".format(url, exc.reason)) from exc
    except ValueError as exc:
        raise UpdateCheckError("GET {} returned invalid JSON".format(url)) from exc


def _asset_map(release):
    return {
        asset.get("name"): asset.get("browser_download_url")
        for asset in release.get("assets", [])
    }


def candidate_tags(releases):
    """Yield ``(version, release)`` for well-formed skill tags, newest release first."""
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


def check_for_update(repo=DEFAULT_REPO, root=None, manifest_url=None, timeout=20):
    """Compare the local skill version with the newest published release."""
    current = read_local_version(root)

    if manifest_url:
        manifest = fetch_json(manifest_url, timeout=timeout)
        latest = str(manifest.get("version") or "").strip()
        if not latest:
            raise UpdateCheckError("manifest {} has no version".format(manifest_url))
        assets = manifest.get("assets") or manifest
        zip_url = manifest.get("zip_url") or (assets or {}).get("zip")
        sha_url = manifest.get("sha256_url") or (assets or {}).get("sha256")
        tag = manifest.get("tag")
        release_url = manifest.get("release_url")
        published_at = manifest.get("released_at")
    else:
        releases = fetch_json(RELEASES_API.format(repo=repo), timeout=timeout)
        if not isinstance(releases, list):
            raise UpdateCheckError("unexpected release payload from GitHub")
        found = newest_release(releases)
        if found is None:
            raise UpdateCheckError("no {}{{version}} release found in {}".format(TAG_PREFIX, repo))
        latest, release = found
        assets = _asset_map(release)
        zip_url = next((url for name, url in assets.items()
                        if name and name.endswith(".zip")), None)
        sha_url = next((url for name, url in assets.items()
                        if name and name.endswith(".sha256")), None)
        tag = release.get("tag_name")
        release_url = release.get("html_url")
        published_at = release.get("published_at")

    return {
        "name": SKILL_NAME,
        "repo": repo,
        "current": current,
        "latest": latest,
        "update_available": compare_versions(latest, current) > 0,
        "tag": tag,
        "release_url": release_url,
        "zip_url": zip_url,
        "sha256_url": sha_url,
        "released_at": published_at,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check whether a newer skill release exists.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name (default: %(default)s)")
    parser.add_argument("--manifest-url", default=None,
                        help="read a published latest.json instead of the GitHub API")
    parser.add_argument("--version-file", default=None, help="override the local VERSION path")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--exit-code", action="store_true",
                        help="exit 3 when an update is available")
    parser.add_argument("--quiet", action="store_true", help="print nothing, only set the code")
    args = parser.parse_args(argv)

    root = Path(args.version_file).resolve().parent if args.version_file else None
    try:
        report = check_for_update(repo=args.repo, root=root,
                                  manifest_url=args.manifest_url, timeout=args.timeout)
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
