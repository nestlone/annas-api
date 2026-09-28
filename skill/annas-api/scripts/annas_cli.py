"""Command-line front end for the annas-api HTTP service.

Every command prints a single JSON document on stdout so it composes cleanly
with agents and shell pipelines; diagnostics go to stderr. Configuration:

    ANNAS_API_BASE_URL   service root (default https://annas.nestlone.com)
    ANNAS_API_TOKEN      API key (or pass --token / --token-file)

Examples::

    python annas_cli.py health
    python annas_cli.py search "The Great Gatsby" --ext epub --limit 5 --wait
    python annas_cli.py download --md5 <32-hex> --wait --save ./books
    python annas_cli.py jobs --status queued
    python annas_cli.py cancel <job-id>
    python annas_cli.py update
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from annas_client import DEFAULT_BASE_URL, AnnasApiError, AnnasClient  # noqa: E402

EXIT_OK = 0
EXIT_API_ERROR = 1
EXIT_USAGE = 2


def _emit(payload, pretty):
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2 if pretty else None)
    sys.stdout.write("\n")


def _build_client(args):
    token = args.token
    if not token and args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    return AnnasClient(base_url=args.base_url, token=token, timeout=args.http_timeout)


def _progress(job):
    print("  ... {} {}".format(job["id"], job["status"]), file=sys.stderr, flush=True)


def cmd_health(client, args):
    return client.health()


def cmd_search(client, args):
    job = client.search(args.query, ext=args.ext, limit=args.limit)
    if args.wait:
        job = client.wait(job["id"], timeout=args.timeout, on_poll=_progress)
    return job


def cmd_download(client, args):
    if bool(args.md5) == bool(args.url):
        raise SystemExit("error: provide exactly one of --md5 or --url")
    job = client.download(md5=args.md5, direct_url=args.url, name=args.name)
    if args.wait:
        job = client.wait(job["id"], timeout=args.timeout, on_poll=_progress)
    if job.get("status") == "completed" and job.get("download_url") and args.save:
        written = client.save(job["download_url"], args.save)
        job = dict(job, saved_to=str(written))
    return job


def cmd_job(client, args):
    return client.get_job(args.job_id)


def cmd_jobs(client, args):
    return client.list_jobs(status=args.status, limit=args.limit, offset=args.offset)


def cmd_cancel(client, args):
    return client.cancel(args.job_id)


def cmd_update(client, args):
    from check_update import check_for_update

    report = check_for_update(repo=args.repo)
    if report["update_available"] and not args.quiet:
        print("update available: {} -> {}".format(report["current"], report["latest"]),
              file=sys.stderr)
    return report


def build_parser():
    parser = argparse.ArgumentParser(
        prog="annas-cli",
        description="Client for an annas-api deployment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default=os.environ.get("ANNAS_API_BASE_URL", DEFAULT_BASE_URL),
                        help="service root (default: %(default)s)")
    parser.add_argument("--token", default=None, help="API key; overrides ANNAS_API_TOKEN")
    parser.add_argument("--token-file", default=None, help="read the API key from this file")
    parser.add_argument("--http-timeout", type=float, default=60, help="per-request timeout (s)")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON output")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="check service liveness").set_defaults(func=cmd_health)

    p = sub.add_parser("search", help="enqueue a catalog search")
    p.add_argument("query")
    p.add_argument("--ext", default=None, help="format filter, e.g. epub")
    p.add_argument("--limit", type=int, default=10, help="1-50 (default: %(default)s)")
    p.add_argument("--wait", action="store_true", help="poll until the job finishes")
    p.add_argument("--timeout", type=float, default=600, help="poll budget in seconds")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("download", help="enqueue a download from an md5 or direct URL")
    p.add_argument("--md5", default=None, help="32-character hex digest")
    p.add_argument("--url", default=None, help="public HTTPS direct URL")
    p.add_argument("--name", default=None, help="optional filename hint")
    p.add_argument("--wait", action="store_true", help="poll until the job finishes")
    p.add_argument("--timeout", type=float, default=1800, help="poll budget in seconds")
    p.add_argument("--save", default=None, help="with --wait, save the file to this path/dir")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("job", help="show one job")
    p.add_argument("job_id")
    p.set_defaults(func=cmd_job)

    p = sub.add_parser("jobs", help="list jobs, newest first")
    p.add_argument("--status", default=None,
                   choices=["queued", "running", "completed", "failed", "cancelled"])
    p.add_argument("--limit", type=int, default=50, help="1-100 (default: %(default)s)")
    p.add_argument("--offset", type=int, default=0)
    p.set_defaults(func=cmd_jobs)

    p = sub.add_parser("cancel", help="cancel a queued job")
    p.add_argument("job_id")
    p.set_defaults(func=cmd_cancel)

    p = sub.add_parser("update", help="check whether a newer skill release exists")
    p.add_argument("--repo", default="nestlone/annas-api")
    p.add_argument("--quiet", action="store_true", help="suppress the stderr notice")
    p.set_defaults(func=cmd_update)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    client = _build_client(args)
    try:
        _emit(args.func(client, args), args.pretty)
    except AnnasApiError as exc:
        print("error: {} (status={})".format(exc, exc.status), file=sys.stderr)
        return EXIT_API_ERROR
    except ValueError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
