import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from annas_api.api import create_app
from annas_api.jobs import BrowserUnavailable, JobService


class JobServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.jobs = JobService(self.folder.name, workers=1)
        self.addCleanup(self.jobs.close)

    def wait_for(self, job_id):
        for _ in range(200):
            job = self.jobs.get(job_id)
            if job["status"] in {"completed", "failed", "cancelled"}:
                return job
            time.sleep(0.01)
        self.fail("job did not complete")

    def block_worker(self, started, gate, result=None):
        """Patch search_books so the single worker parks until `gate` is set."""

        def run(*args, **kwargs):
            started.set()
            gate.wait(timeout=5)
            return result if result is not None else []

        return patch("annas_api.jobs.search_books", side_effect=run)

    def test_search_job_persists_result(self):
        with patch("annas_api.jobs.search_books", return_value=[{"title": "Example"}]):
            job_id = self.jobs.submit_search("example")
            job = self.wait_for(job_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"], [{"title": "Example"}])

    def test_browser_dependent_jobs_are_rejected_before_queueing(self):
        self.jobs.browser_error = "browser is unavailable"
        with self.assertRaises(BrowserUnavailable):
            self.jobs.submit_search("example")
        with self.assertRaises(BrowserUnavailable):
            self.jobs.submit_download(md5="0" * 32)
        self.assertEqual(self.jobs.list_jobs(), [])

    def test_direct_url_download_does_not_require_browser(self):
        self.jobs.browser_error = "browser is unavailable"
        def fake_download(**kwargs):
            target = Path(kwargs["output_dir"]) / "book.pdf"
            target.write_bytes(b"content")
            return str(target)

        with patch("annas_api.jobs.validate_public_https"), patch(
            "annas_api.jobs.download_book", side_effect=fake_download
        ):
            job_id = self.jobs.submit_download(direct_url="https://download.example/book.pdf")
            self.assertEqual(self.wait_for(job_id)["status"], "completed")

    def test_download_job_serves_only_its_own_file(self):
        def fake_download(**kwargs):
            target = Path(kwargs["output_dir"]) / "book.epub"
            target.write_bytes(b"content")
            return str(target)

        with patch("annas_api.jobs.download_book", side_effect=fake_download):
            job_id = self.jobs.submit_download(md5="0" * 32)
            job = self.wait_for(job_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(self.jobs.completed_file(job_id).read_bytes(), b"content")

    def test_cancel_queued_job_sets_cancelled(self):
        started, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        with self.block_worker(started, gate):
            running_id = self.jobs.submit_search("first")
            self.assertTrue(started.wait(5))
            queued_id = self.jobs.submit_search("second")
            self.assertEqual(self.jobs.get(queued_id)["status"], "queued")

            self.assertEqual(self.jobs.cancel(queued_id), "cancelled")
            self.assertEqual(self.jobs.get(queued_id)["status"], "cancelled")

            gate.set()
            self.assertEqual(self.wait_for(running_id)["status"], "completed")
        # The worker must never have revived the cancelled row.
        self.assertEqual(self.jobs.get(queued_id)["status"], "cancelled")

    def test_cancel_running_job_is_rejected(self):
        started, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        with self.block_worker(started, gate):
            job_id = self.jobs.submit_search("running")
            self.assertTrue(started.wait(5))
            self.assertEqual(self.jobs.get(job_id)["status"], "running")

            self.assertEqual(self.jobs.cancel(job_id), "running")
            self.assertEqual(self.jobs.get(job_id)["status"], "running")

            gate.set()
            self.assertEqual(self.wait_for(job_id)["status"], "completed")

    def test_cancel_completed_job_is_rejected(self):
        with patch("annas_api.jobs.search_books", return_value=[]):
            job_id = self.jobs.submit_search("done")
            self.wait_for(job_id)
        self.assertEqual(self.jobs.cancel(job_id), "terminal")
        self.assertEqual(self.jobs.get(job_id)["status"], "completed")

    def test_cancel_unknown_job_returns_not_found(self):
        self.assertEqual(self.jobs.cancel("missing"), "not_found")

    def test_cancel_is_idempotent_after_first_cancel(self):
        started, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        with self.block_worker(started, gate):
            held_id = self.jobs.submit_search("hold")
            self.assertTrue(started.wait(5))
            queued_id = self.jobs.submit_search("cancel me")
            self.assertEqual(self.jobs.cancel(queued_id), "cancelled")
            self.assertEqual(self.jobs.cancel(queued_id), "terminal")
            gate.set()
            self.wait_for(held_id)

    def test_list_jobs_filters_orders_and_paginates(self):
        started, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        with self.block_worker(started, gate):
            first = self.jobs.submit_search("first")  # blocks the only worker
            self.assertTrue(started.wait(5))
            second = self.jobs.submit_search("second")
            third = self.jobs.submit_search("third")

            queued = self.jobs.list_jobs(status="queued")
            self.assertEqual([job["id"] for job in queued], [third, second])

            page = self.jobs.list_jobs(status="queued", limit=1)
            self.assertEqual([job["id"] for job in page], [third])
            tail = self.jobs.list_jobs(status="queued", limit=1, offset=1)
            self.assertEqual([job["id"] for job in tail], [second])

            running = self.jobs.list_jobs(status="running")
            self.assertEqual([job["id"] for job in running], [first])
            self.assertEqual(self.jobs.list_jobs()[:1][0]["id"], third)

            gate.set()
            for job_id in (first, second, third):
                self.wait_for(job_id)

    def test_list_jobs_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            self.jobs.list_jobs(status="bogus")

    def test_purge_expired_removes_files_and_rows(self):
        def fake_download(**kwargs):
            target = Path(kwargs["output_dir"]) / "book.epub"
            target.write_bytes(b"content")
            return str(target)

        with patch("annas_api.jobs.download_book", side_effect=fake_download):
            job_id = self.jobs.submit_download(md5="0" * 32)
            self.wait_for(job_id)
        job_dir = self.jobs.download_dir / job_id
        self.assertTrue(job_dir.is_dir())

        # Freshly finished: inside the window, nothing is removed.
        self.assertEqual(self.jobs.purge_expired(), 0)
        self.assertIsNotNone(self.jobs.get(job_id))

        future = int(time.time()) + self.jobs.retention_seconds + 1
        self.assertEqual(self.jobs.purge_expired(now=future), 1)
        self.assertIsNone(self.jobs.get(job_id))
        self.assertFalse(job_dir.exists())

    def test_purge_expired_never_touches_active_jobs(self):
        started, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        with self.block_worker(started, gate):
            job_id = self.jobs.submit_search("hold")
            self.assertTrue(started.wait(5))
            # A far-future cutoff must still leave the running job alone.
            self.assertEqual(self.jobs.purge_expired(now=int(time.time()) + 10 ** 6), 0)
            self.assertIsNotNone(self.jobs.get(job_id))
            gate.set()
            self.wait_for(job_id)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.settings = SimpleNamespace(
            data_dir=Path(self.folder.name),
            workers=1,
            api_token="token",
            signing_key="secret",
            file_url_ttl=300,
            retention_seconds=24 * 3600,
            public_base_url="",
        )

    def wait_until_done(self, client, job_id, attempts=100):
        for _ in range(attempts):
            response = client.get(f"/v1/jobs/{job_id}", headers={"X-API-Key": "token"})
            if response.json()["status"] in {"completed", "failed", "cancelled"}:
                return response
            time.sleep(0.01)
        self.fail("job did not finish")

    def test_requires_token_and_returns_completed_search(self):
        with patch("annas_api.jobs.search_books", return_value=[]):
            with TestClient(create_app(self.settings)) as client:
                denied = client.post("/v1/search", json={"query": "example"})
                self.assertEqual(denied.status_code, 401)
                created = client.post("/v1/search", headers={"X-API-Key": "token"}, json={"query": "example"})
                self.assertEqual(created.status_code, 202)
                job_id = created.json()["id"]
                for _ in range(100):
                    response = client.get(f"/v1/jobs/{job_id}", headers={"X-API-Key": "token"})
                    if response.json()["status"] == "completed":
                        break
                    time.sleep(0.01)
                self.assertEqual(response.json()["result"], [])

    def test_browser_error_is_reported_as_service_unavailable(self):
        self.settings.browser_error = "browser is unavailable"
        with TestClient(create_app(self.settings)) as client:
            health = client.get("/healthz")
            self.assertEqual(health.status_code, 503)
            self.assertEqual(health.json()["status"], "degraded")
            response = client.post("/v1/search", headers={"X-API-Key": "token"}, json={"query": "example"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"], "browser is unavailable")

    def test_links_fall_back_to_request_host_when_unset(self):
        with patch("annas_api.jobs.search_books", return_value=[]):
            with TestClient(create_app(self.settings)) as client:
                created = client.post("/v1/search", headers={"X-API-Key": "token"}, json={"query": "example"})
                self.assertTrue(created.json()["status_url"].startswith("http://testserver/v1/jobs/"))
                self.wait_until_done(client, created.json()["id"])

    def test_public_base_url_replaces_the_request_host(self):
        self.settings.public_base_url = "https://annas.example.com"
        with patch("annas_api.jobs.search_books", return_value=[]):
            with TestClient(create_app(self.settings)) as client:
                created = client.post("/v1/search", headers={"X-API-Key": "token"}, json={"query": "example"})
                self.assertTrue(
                    created.json()["status_url"].startswith("https://annas.example.com/v1/jobs/")
                )
                self.wait_until_done(client, created.json()["id"])

    def test_public_base_url_applies_to_download_url(self):
        self.settings.public_base_url = "https://annas.example.com"

        def fake_download(**kwargs):
            target = Path(kwargs["output_dir"]) / "book.epub"
            target.write_bytes(b"content")
            return str(target)

        with patch("annas_api.jobs.download_book", side_effect=fake_download):
            with TestClient(create_app(self.settings)) as client:
                created = client.post("/v1/downloads", headers={"X-API-Key": "token"}, json={"md5": "0" * 32})
                job_id = created.json()["id"]
                for _ in range(100):
                    response = client.get(f"/v1/jobs/{job_id}", headers={"X-API-Key": "token"})
                    if response.json()["status"] == "completed":
                        break
                    time.sleep(0.01)
                url = response.json()["download_url"]
                self.assertTrue(url.startswith("https://annas.example.com/v1/files/"), url)

    def test_jobs_list_requires_token(self):
        with TestClient(create_app(self.settings)) as client:
            self.assertEqual(client.get("/v1/jobs").status_code, 401)

    def test_jobs_list_rejects_unknown_status(self):
        with TestClient(create_app(self.settings)) as client:
            response = client.get("/v1/jobs?status=bogus", headers={"X-API-Key": "token"})
            self.assertEqual(response.status_code, 422)

    def test_jobs_list_and_cancel_flow(self):
        started, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)

        def hold(*args, **kwargs):
            started.set()
            gate.wait(timeout=5)
            return []

        with patch("annas_api.jobs.search_books", side_effect=hold):
            with TestClient(create_app(self.settings)) as client:
                headers = {"X-API-Key": "token"}
                running_id = client.post("/v1/search", headers=headers, json={"query": "first"}).json()["id"]
                self.assertTrue(started.wait(5))
                queued_id = client.post("/v1/search", headers=headers, json={"query": "second"}).json()["id"]

                listed = client.get("/v1/jobs?status=queued&limit=50", headers=headers)
                self.assertEqual(listed.status_code, 200)
                body = listed.json()
                self.assertEqual([job["id"] for job in body["jobs"]], [queued_id])
                self.assertNotIn("result", body["jobs"][0])

                cancelled = client.post(f"/v1/jobs/{queued_id}/cancel", headers=headers)
                self.assertEqual(cancelled.status_code, 200)
                self.assertEqual(cancelled.json()["status"], "cancelled")

                again = client.post(f"/v1/jobs/{queued_id}/cancel", headers=headers)
                self.assertEqual(again.status_code, 409)
                self.assertIn("已结束", again.json()["detail"])

                missing = client.post("/v1/jobs/does-not-exist/cancel", headers=headers)
                self.assertEqual(missing.status_code, 404)

                gate.set()
                for _ in range(200):
                    if client.get(f"/v1/jobs/{running_id}", headers=headers).json()["status"] == "completed":
                        break
                    time.sleep(0.01)

    def test_cancel_running_returns_409(self):
        started, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)

        def hold(*args, **kwargs):
            started.set()
            gate.wait(timeout=5)
            return []

        with patch("annas_api.jobs.search_books", side_effect=hold):
            with TestClient(create_app(self.settings)) as client:
                headers = {"X-API-Key": "token"}
                job_id = client.post("/v1/search", headers=headers, json={"query": "hold"}).json()["id"]
                self.assertTrue(started.wait(5))

                blocked = client.post(f"/v1/jobs/{job_id}/cancel", headers=headers)
                self.assertEqual(blocked.status_code, 409)
                self.assertIn("正在执行", blocked.json()["detail"])

                gate.set()
                for _ in range(200):
                    if client.get(f"/v1/jobs/{job_id}", headers=headers).json()["status"] == "completed":
                        break
                    time.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
