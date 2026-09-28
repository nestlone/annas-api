import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from annas_api.accounts import AccountStore
from annas_api.api import create_app

from _fixtures import fake_settings

ADMIN = {"username": "admin", "password": "adminpass123"}

# The job runs in a worker thread, so a patch must stay active until the queue
# drains. Every test below submits and settles inside the same `with` block.


def fake_download(**kwargs):
    target = Path(kwargs["output_dir"]) / "book.epub"
    target.write_bytes(b"content")
    return str(target)


def stub_search():
    return patch("annas_api.jobs.search_books", return_value=[])


def stub_download():
    return patch("annas_api.jobs.download_book", side_effect=fake_download)


class QuotaApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def make_client(self, **overrides):
        return TestClient(create_app(fake_settings(self.folder.name, **overrides)))

    def login(self, client, username, password):
        response = client.post("/web/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)

    def drain(self, client, headers=None):
        """Wait for every queued or running job to reach a terminal state."""
        for _ in range(400):
            response = client.get("/v1/jobs?limit=100", headers=headers or {})
            self.assertEqual(response.status_code, 200, response.text)
            jobs = response.json()["jobs"]
            if not [job for job in jobs if job["status"] in {"queued", "running"}]:
                return
            time.sleep(0.01)
        self.fail("jobs did not settle")

    def search(self, client, query="example"):
        return client.post("/v1/search", json={"query": query})

    def download(self, client):
        return client.post("/v1/downloads", json={"md5": "0" * 32})

    def create_user(self, client, quota=None, username="bob"):
        created = client.post(
            "/web/admin/users", json={"username": username, "password": "password123"}
        )
        self.assertEqual(created.status_code, 201, created.text)
        user = created.json()
        if quota is not None:
            response = client.put(f"/web/admin/users/{user['id']}/quota", json=quota)
            self.assertEqual(response.status_code, 200, response.text)
        return user

    def test_daily_search_quota_is_enforced(self):
        with self.make_client(admin_password=ADMIN["password"]) as client:
            self.login(client, ADMIN["username"], ADMIN["password"])
            self.create_user(client, {"daily_searches": 2, "daily_downloads": 0, "max_concurrent_jobs": 0})
            client.post("/web/logout")

            self.login(client, "bob", "password123")
            with stub_search():
                for _ in range(2):
                    self.assertEqual(self.search(client).status_code, 202)
                refused = self.search(client)
                self.assertEqual(refused.status_code, 429)
                self.assertIn("上限", refused.json()["detail"])
                self.drain(client)

            usage = client.get("/web/usage").json()["usage"]
            self.assertEqual(usage["searches"], 2, "a refused submit must not be counted")

    def test_search_and_download_quotas_are_independent(self):
        with self.make_client(admin_password=ADMIN["password"]) as client:
            self.login(client, ADMIN["username"], ADMIN["password"])
            self.create_user(client, {"daily_searches": 1, "daily_downloads": 1, "max_concurrent_jobs": 0})
            client.post("/web/logout")

            self.login(client, "bob", "password123")
            with stub_search(), stub_download():
                self.assertEqual(self.search(client).status_code, 202)
                self.assertEqual(self.search(client).status_code, 429)
                self.assertEqual(self.download(client).status_code, 202)
                self.assertEqual(self.download(client).status_code, 429)
                self.drain(client)

            usage = client.get("/web/usage").json()["usage"]
            self.assertEqual((usage["searches"], usage["downloads"]), (1, 1))

    def test_zero_means_unlimited(self):
        with self.make_client(admin_password=ADMIN["password"]) as client:
            self.login(client, ADMIN["username"], ADMIN["password"])
            self.create_user(client, {"daily_searches": 0, "daily_downloads": 0, "max_concurrent_jobs": 0})
            client.post("/web/logout")

            self.login(client, "bob", "password123")
            with stub_search():
                for _ in range(4):
                    self.assertEqual(self.search(client).status_code, 202)
                self.drain(client)
            self.assertEqual(client.get("/web/usage").json()["usage"]["searches"], 4)

    def test_max_concurrent_jobs_is_enforced(self):
        with self.make_client(admin_password=ADMIN["password"]) as client:
            self.login(client, ADMIN["username"], ADMIN["password"])
            self.create_user(client, {"daily_searches": 0, "daily_downloads": 0, "max_concurrent_jobs": 1})
            client.post("/web/logout")

            self.login(client, "bob", "password123")
            started, gate = threading.Event(), threading.Event()

            def hold(*args, **kwargs):
                started.set()
                gate.wait(timeout=5)
                return []

            with patch("annas_api.jobs.search_books", side_effect=hold):
                self.assertEqual(self.search(client, "one").status_code, 202)
                self.assertTrue(started.wait(5), "the worker never picked up the first job")
                second = self.search(client, "two")
                self.assertEqual(second.status_code, 429)
                self.assertIn("同时进行", second.json()["detail"])
                gate.set()
                self.drain(client)

    def test_the_env_token_is_not_quota_limited(self):
        headers = {"X-API-Key": "env-token"}
        with self.make_client(api_token="env-token", admin_password=ADMIN["password"]) as client:
            with stub_search():
                for _ in range(4):
                    response = client.post("/v1/search", json={"query": "x"}, headers=headers)
                    self.assertEqual(response.status_code, 202)
                self.drain(client, headers)

    def test_jobs_record_their_owner(self):
        with self.make_client(admin_password=ADMIN["password"]) as client:
            self.login(client, ADMIN["username"], ADMIN["password"])
            bob = self.create_user(client)
            client.post("/web/logout")

            self.login(client, "bob", "password123")
            with stub_search():
                job_id = self.search(client).json()["id"]
                self.drain(client)

            connection = AccountStore(self.folder.name)._connect()
            try:
                row = connection.execute(
                    "SELECT owner_id FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(row["owner_id"], bob["id"])

    def test_admin_can_reset_a_users_usage(self):
        with self.make_client(admin_password=ADMIN["password"]) as client:
            self.login(client, ADMIN["username"], ADMIN["password"])
            bob = self.create_user(client, {"daily_searches": 1, "daily_downloads": 0, "max_concurrent_jobs": 0})
            client.post("/web/logout")

            self.login(client, "bob", "password123")
            with stub_search():
                self.assertEqual(self.search(client).status_code, 202)
                self.assertEqual(self.search(client).status_code, 429)
                self.drain(client)
            client.post("/web/logout")

            self.login(client, ADMIN["username"], ADMIN["password"])
            reset = client.post(f"/web/admin/users/{bob['id']}/usage/reset")
            self.assertEqual(reset.status_code, 200)
            self.assertEqual(reset.json()["searches"], 0)
            client.post("/web/logout")

            self.login(client, "bob", "password123")
            with stub_search():
                self.assertEqual(self.search(client).status_code, 202)
                self.drain(client)

    def test_quota_changes_are_visible_in_the_admin_listing(self):
        with self.make_client(admin_password=ADMIN["password"]) as client:
            self.login(client, ADMIN["username"], ADMIN["password"])
            self.create_user(client, {"daily_searches": 7, "daily_downloads": 3, "max_concurrent_jobs": 2})
            listing = client.get("/web/admin/users").json()
            bob = [user for user in listing["users"] if user["username"] == "bob"][0]
            self.assertEqual(bob["quota"]["daily_searches"], 7)
            self.assertEqual(bob["quota"]["daily_downloads"], 3)
            self.assertEqual(bob["quota"]["max_concurrent_jobs"], 2)
