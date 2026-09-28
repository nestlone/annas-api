"""Offline tests for the skill's client, CLI helpers, and update checker.

A standard-library ``http.server`` stub stands in for the real deployment, so
these tests exercise the full HTTP contract without a network or an API key.

Run from the repository root::

    python -m unittest discover -s skill/annas-api/tests -v
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_update  # noqa: E402
import annas_cli  # noqa: E402
from annas_client import AnnasApiError, AnnasClient  # noqa: E402

API_KEY = "test-token"
FILE_BYTES = b"STUB-EPUB-CONTENTS"
MANIFEST_URL_PATH = "/latest.json"
KNOWN_STATUSES = ("queued", "running", "completed", "failed", "cancelled")


class _Handler(BaseHTTPRequestHandler):
    jobs = {}
    counter = 0
    lock = threading.Lock()

    def log_message(self, *args):        # keep test output clean
        pass

    # -- helpers -----------------------------------------------------------

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if self.headers.get("X-API-Key") == API_KEY:
            return True
        self._send_json(401, {"detail": "缺少或无效的 API 密钥"})
        return False

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def _new_job(self, kind):
        with _Handler.lock:
            _Handler.counter += 1
            job_id = "job-{}".format(_Handler.counter)
        job = {
            "id": job_id, "kind": kind, "status": "queued", "result": None,
            "error": None, "created_at": 1790000000, "updated_at": 1790000000,
        }
        _Handler.jobs[job_id] = job
        return job

    # -- routes ------------------------------------------------------------

    def do_GET(self):
        path, _, query = self.path.partition("?")

        if path == "/healthz":
            return self._send_json(200, {"status": "ok"})
        if path == MANIFEST_URL_PATH:
            return self._send_json(200, {
                "name": "annas-api", "version": "9.9.9", "tag": "skill-v9.9.9",
                "zip_url": "https://example.invalid/annas-api-skill-9.9.9.zip",
                "sha256_url": "https://example.invalid/annas-api-skill-9.9.9.zip.sha256",
                "release_url": "https://example.invalid/releases/skill-v9.9.9",
                "released_at": "2026-09-28T00:00:00Z",
            })
        if path == "/latest-same.json":
            return self._send_json(200, {"name": "annas-api",
                                         "version": check_update.read_local_version()})
        if path == "/VERSION":
            return self._send_bytes(200, check_update.read_local_version().encode() + b"\n")
        if path == "/VERSION-newer":
            return self._send_bytes(200, b"9.9.9\n")
        if path == "/VERSION-garbage":
            return self._send_bytes(200, b"not-a-version\n")
        if path.startswith("/v1/files/"):
            job_id = path.rsplit("/", 1)[-1]
            job = _Handler.jobs.get(job_id) or {}
            if job.get("status") != "completed":
                return self._send_json(404, {"detail": "文件不存在或任务未完成"})
            self.send_response(200)
            self.send_header("Content-Type", "application/epub+zip")
            self.send_header("Content-Disposition", 'attachment; filename="stub.epub"')
            self.send_header("Content-Length", str(len(FILE_BYTES)))
            self.end_headers()
            return self.wfile.write(FILE_BYTES)

        if not path.startswith("/v1/"):
            return self._send_json(404, {"detail": "not found"})
        if not self._authorized():
            return

        if path == "/v1/jobs":
            params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
            status = params.get("status")
            if status and status not in KNOWN_STATUSES:
                return self._send_json(422, {"detail": "status 必须是 {} 之一".format(
                    ", ".join(KNOWN_STATUSES))})
            limit = int(params.get("limit", 50))
            offset = int(params.get("offset", 0))
            rows = [j for j in _Handler.jobs.values() if not status or j["status"] == status]
            rows = sorted(rows, key=lambda j: j["id"], reverse=True)
            page = rows[offset:offset + limit]
            summaries = [{k: v for k, v in j.items() if k != "result"} for j in page]
            return self._send_json(200, {"jobs": summaries, "count": len(summaries),
                                         "limit": limit, "offset": offset})

        if path.startswith("/v1/jobs/"):
            job_id = path[len("/v1/jobs/"):]
            job = _Handler.jobs.get(job_id)
            if job is None:
                return self._send_json(404, {"detail": "任务不存在"})
            payload = dict(job)
            if job["status"] == "completed" and job["kind"] == "download":
                payload["download_url"] = "{}/v1/files/{}?expires=1&signature=x".format(
                    self.server.public_root, job_id)
            return self._send_json(200, payload)

        return self._send_json(404, {"detail": "not found"})

    def do_POST(self):
        path = self.path.partition("?")[0]
        if not path.startswith("/v1/"):
            return self._send_json(404, {"detail": "not found"})
        if not self._authorized():
            return

        if path == "/v1/search":
            body = self._body()
            if not body.get("query"):
                return self._send_json(422, {"detail": "query required"})
            job = self._new_job("search")
            job["status"] = "completed"
            job["result"] = [{"md5": "bcdcd8bd16771a4f03c71b89a490dd53",
                              "title": body["query"], "format": body.get("ext") or "epub"}]
            return self._send_json(202, {"id": job["id"], "status": "queued",
                                         "status_url": "/v1/jobs/{}".format(job["id"])})

        if path == "/v1/downloads":
            body = self._body()
            if bool(body.get("md5")) == bool(body.get("direct_url")):
                return self._send_json(422, {"detail": "exactly one of md5/direct_url"})
            job = self._new_job("download")
            job["status"] = "completed"
            return self._send_json(202, {"id": job["id"], "status": "queued",
                                         "status_url": "/v1/jobs/{}".format(job["id"])})

        if path.startswith("/v1/jobs/") and path.endswith("/cancel"):
            job_id = path[len("/v1/jobs/"):-len("/cancel")]
            job = _Handler.jobs.get(job_id)
            if job is None:
                return self._send_json(404, {"detail": "任务不存在"})
            if job["status"] != "queued":
                return self._send_json(409, {"detail": "任务已结束，无法取消"})
            job["status"] = "cancelled"
            return self._send_json(200, job)

        return self._send_json(404, {"detail": "not found"})


class StubServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _Handler.jobs = {}
        _Handler.counter = 0
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.server.public_root = "http://127.0.0.1:{}".format(cls.server.server_address[1])
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.base = self.server.public_root
        self.client = AnnasClient(base_url=self.base, token=API_KEY, timeout=10)

    def tearDown(self):
        _Handler.jobs = {}
        _Handler.counter = 0


class ClientTests(StubServerCase):
    def test_health_needs_no_token(self):
        bare = AnnasClient(base_url=self.base, token=None)
        self.assertEqual(bare.health(), {"status": "ok"})

    def test_missing_token_raises_before_request(self):
        with self.assertRaises(AnnasApiError) as ctx:
            AnnasClient(base_url=self.base, token=None).list_jobs()
        self.assertIn("no API token", str(ctx.exception))

    def test_bad_token_maps_to_401(self):
        with self.assertRaises(AnnasApiError) as ctx:
            AnnasClient(base_url=self.base, token="wrong").list_jobs()
        self.assertEqual(ctx.exception.status, 401)

    def test_run_search_returns_completed_result(self):
        job = self.client.run_search("The Great Gatsby", ext="epub", limit=5)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"][0]["format"], "epub")

    def test_invalid_search_maps_to_422(self):
        with self.assertRaises(AnnasApiError) as ctx:
            self.client.search("")
        self.assertEqual(ctx.exception.status, 422)

    def test_download_requires_exactly_one_source(self):
        with self.assertRaises(ValueError):
            self.client.download()
        with self.assertRaises(ValueError):
            self.client.download(md5="a" * 32, direct_url="https://example.invalid/x")

    def test_run_download_and_save_uses_server_filename(self):
        job = self.client.run_download(md5="bcdcd8bd16771a4f03c71b89a490dd53")
        self.assertEqual(job["status"], "completed")
        with tempfile.TemporaryDirectory() as tmp:
            written = self.client.save(job["download_url"], tmp)
            self.assertEqual(written.name, "stub.epub")
            self.assertEqual(written.read_bytes(), FILE_BYTES)

    def test_list_jobs_summaries_omit_result(self):
        self.client.run_search("alpha")
        self.client.run_search("beta")
        page = self.client.list_jobs(limit=10)
        self.assertEqual(page["count"], 2)
        self.assertEqual(page["limit"], 10)
        for item in page["jobs"]:
            self.assertNotIn("result", item)

    def test_list_jobs_status_filter_and_validation(self):
        self.client.run_search("alpha")
        self.assertEqual(self.client.list_jobs(status="running")["count"], 0)
        with self.assertRaises(AnnasApiError) as ctx:
            self.client.list_jobs(status="bogus")
        self.assertEqual(ctx.exception.status, 422)

    def test_cancel_queued_job_succeeds(self):
        with _Handler.lock:
            _Handler.counter += 1
            job_id = "job-{}".format(_Handler.counter)
        _Handler.jobs[job_id] = {"id": job_id, "kind": "search", "status": "queued",
                                 "result": None, "error": None,
                                 "created_at": 1, "updated_at": 1}
        self.assertEqual(self.client.cancel(job_id)["status"], "cancelled")

    def test_cancel_finished_job_maps_to_409(self):
        job = self.client.run_search("gamma")
        with self.assertRaises(AnnasApiError) as ctx:
            self.client.cancel(job["id"])
        self.assertEqual(ctx.exception.status, 409)

    def test_cancel_unknown_maps_to_404(self):
        with self.assertRaises(AnnasApiError) as ctx:
            self.client.cancel("nope")
        self.assertEqual(ctx.exception.status, 404)


class UpdateCheckTests(StubServerCase):
    def test_compare_versions_orders_release_and_prerelease(self):
        self.assertEqual(check_update.compare_versions("1.0.0", "1.0.0"), 0)
        self.assertEqual(check_update.compare_versions("1.0.0", "1.2.0"), -1)
        self.assertEqual(check_update.compare_versions("1.10.0", "1.9.0"), 1)
        self.assertEqual(check_update.compare_versions("1.0.0-rc.1", "1.0.0"), -1)
        self.assertEqual(check_update.compare_versions("2.0.0", "1.9.9"), 1)

    def test_parse_version_rejects_garbage(self):
        with self.assertRaises(check_update.UpdateCheckError):
            check_update.parse_version("not-a-version")

    def test_local_version_is_readable(self):
        self.assertRegex(check_update.read_local_version(), r"^\d+\.\d+\.\d+")

    def test_newest_release_ignores_foreign_tags(self):
        releases = [
            {"tag_name": "v0.0.1", "assets": []},
            {"tag_name": "skill-v1.2.0", "assets": []},
            {"tag_name": "skill-v1.10.0", "assets": []},
            {"tag_name": "skill-vgarbage", "assets": []},
        ]
        version, release = check_update.newest_release(releases)
        self.assertEqual(version, "1.10.0")
        self.assertEqual(release["tag_name"], "skill-v1.10.0")

    def test_check_for_update_against_manifest(self):
        url = self.base + MANIFEST_URL_PATH
        report = check_update.check_for_update(manifest_url=url)
        self.assertTrue(report["update_available"])
        self.assertEqual(report["latest"], "9.9.9")
        self.assertTrue(report["zip_url"].endswith(".zip"))

    def test_check_for_update_reports_up_to_date(self):
        report = check_update.check_for_update(manifest_url=self.base + "/latest-same.json")
        self.assertFalse(report["update_available"])

    def test_manifest_without_version_is_rejected(self):
        with self.assertRaises(check_update.UpdateCheckError):
            check_update.check_for_update(manifest_url=self.base + "/v1/nope")

    def test_source_manifest_requires_a_manifest_url(self):
        with self.assertRaises(check_update.UpdateCheckError):
            check_update.check_for_update(source="manifest")

    def test_raw_version_source_reports_up_to_date(self):
        # The stub has no releases API, so asset enrichment must fail harmlessly.
        with mock.patch.object(check_update, "RELEASES_API", self.base + "/api/releases"):
            report = check_update.check_for_update(version_url=self.base + "/VERSION")
        self.assertFalse(report["update_available"])
        self.assertEqual(report["latest"], check_update.read_local_version())
        self.assertEqual(report["source"], "version")
        self.assertEqual(report["tag"], "skill-v" + report["latest"])
        self.assertIsNone(report["zip_url"])

    def test_raw_version_source_detects_a_newer_release(self):
        with mock.patch.object(check_update, "RELEASES_API", self.base + "/api/releases"):
            report = check_update.check_for_update(version_url=self.base + "/VERSION-newer")
        self.assertTrue(report["update_available"])
        self.assertEqual(report["latest"], "9.9.9")
        self.assertIn("skill-v9.9.9", report["release_url"])

    def test_raw_version_source_rejects_garbage(self):
        with self.assertRaises(check_update.UpdateCheckError):
            check_update.check_for_update(version_url=self.base + "/VERSION-garbage")


class CliTests(StubServerCase):
    def run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = annas_cli.main(argv)
        self.stderr = stderr.getvalue()          # assertions can inspect diagnostics
        return code, stdout.getvalue()

    def test_global_flags_work_after_the_subcommand(self):
        code, out = self.run_cli(["--base-url", self.base, "--token", API_KEY,
                                  "jobs", "--pretty"])
        self.assertEqual(code, 0)
        self.assertIn("\n  ", out)                       # --pretty did apply
        self.assertEqual(json.loads(out)["count"], 0)

    def test_global_flags_work_before_the_subcommand(self):
        code, out = self.run_cli(["jobs", "--base-url", self.base, "--token", API_KEY])
        self.assertEqual(code, 0)
        self.assertNotIn("\n  ", out)                    # compact by default
        self.assertEqual(json.loads(out)["count"], 0)

    def test_search_command_emits_the_submitted_job(self):
        code, out = self.run_cli(["search", "Ulysses", "--ext", "epub", "--limit", "2",
                                  "--wait", "--base-url", self.base, "--token", API_KEY])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["result"][0]["title"], "Ulysses")

    def test_health_command_needs_no_token(self):
        code, out = self.run_cli(["health", "--base-url", self.base])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"status": "ok"})

    def test_token_file_is_read(self):
        with tempfile.NamedTemporaryFile("w", suffix=".token", delete=False) as handle:
            handle.write(API_KEY + "\n")
            path = handle.name
        self.addCleanup(os.unlink, path)
        code, out = self.run_cli(["jobs", "--base-url", self.base, "--token-file", path])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["count"], 0)

    def test_cancel_unknown_job_exits_nonzero(self):
        code, out = self.run_cli(["cancel", "nope", "--base-url", self.base,
                                  "--token", API_KEY])
        self.assertEqual(code, annas_cli.EXIT_API_ERROR)
        self.assertEqual(out, "")

    def test_unknown_status_is_a_usage_error(self):
        with self.assertRaises(SystemExit):
            self.run_cli(["jobs", "--status", "bogus", "--base-url", self.base,
                          "--token", API_KEY])

    def test_bad_credentials_surface_the_status_code(self):
        code, out = self.run_cli(["jobs", "--base-url", self.base, "--token", "wrong"])
        self.assertEqual(code, annas_cli.EXIT_API_ERROR)
        self.assertEqual(out, "")
        self.assertIn("status=401", self.stderr)

    def test_save_without_wait_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.run_cli(["download", "--md5", "a" * 32, "--save", "./books",
                          "--base-url", self.base, "--token", API_KEY])

    def test_download_with_save_writes_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_cli(["download", "--md5", "bcdcd8bd16771a4f03c71b89a490dd53",
                                      "--wait", "--save", tmp,
                                      "--base-url", self.base, "--token", API_KEY])
            self.assertEqual(code, 0)
            saved = json.loads(out)["saved_to"]
            self.assertEqual(Path(saved).read_bytes(), FILE_BYTES)


if __name__ == "__main__":
    unittest.main()
