import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from annas_api.api import create_app

from _fixtures import fake_settings


class WebAuthTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def make_client(self, **overrides):
        return TestClient(create_app(fake_settings(self.folder.name, **overrides)))

    def login(self, client, username, password):
        response = client.post("/web/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def wait_for(self, client, job_id):
        for _ in range(200):
            job = client.get(f"/v1/jobs/{job_id}").json()
            if job["status"] in {"completed", "failed", "cancelled"}:
                return job
            time.sleep(0.01)
        self.fail("job did not finish")

    def test_setup_required_on_a_fresh_database(self):
        with self.make_client() as client:
            data = client.get("/web/settings").json()
            self.assertTrue(data["setup_required"])
            self.assertFalse(data["registration_open"])

    def test_first_account_bootstraps_an_administrator(self):
        with self.make_client() as client:
            created = client.post(
                "/web/register", json={"username": "alice", "password": "password123"}
            )
            self.assertEqual(created.status_code, 201, created.text)
            self.assertTrue(created.json()["is_admin"])
            self.assertEqual(client.get("/web/me").json()["username"], "alice")
            # The console is now reachable, and setup is no longer offered.
            self.assertFalse(client.get("/web/settings").json()["setup_required"])

    def test_logout_invalidates_the_session(self):
        with self.make_client() as client:
            client.post("/web/register", json={"username": "alice", "password": "password123"})
            self.assertEqual(client.get("/web/me").status_code, 200)
            client.post("/web/logout")
            self.assertEqual(client.get("/web/me").status_code, 401)

    def test_user_can_change_password_and_all_sessions_are_invalidated(self):
        with self.make_client() as client:
            client.post("/web/register", json={"username": "alice", "password": "password123"})
            changed = client.post("/web/password", json={
                "current_password": "password123", "new_password": "changed123",
            })
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertEqual(client.get("/web/me").status_code, 401)
            self.assertEqual(
                client.post("/web/login", json={"username": "alice", "password": "password123"}).status_code,
                401,
            )
            self.assertEqual(
                client.post("/web/login", json={"username": "alice", "password": "changed123"}).status_code,
                200,
            )

    def test_password_change_requires_the_current_password(self):
        with self.make_client() as client:
            client.post("/web/register", json={"username": "alice", "password": "password123"})
            self.assertEqual(client.post("/web/password", json={
                "current_password": "wrong-password", "new_password": "changed123",
            }).status_code, 401)

    def test_registration_is_closed_by_default_once_an_admin_exists(self):
        with self.make_client(admin_password="adminpass123") as client:
            denied = client.post(
                "/web/register", json={"username": "bob", "password": "password123"}
            )
            self.assertEqual(denied.status_code, 403)

    def test_admin_can_open_and_close_registration(self):
        with self.make_client(admin_password="adminpass123") as client:
            self.login(client, "admin", "adminpass123")
            opened = client.patch("/web/admin/settings", json={"registration_open": True})
            self.assertTrue(opened.json()["registration_open"])
            allowed = client.post(
                "/web/register", json={"username": "bob", "password": "password123"}
            )
            self.assertEqual(allowed.status_code, 201)
            self.assertFalse(allowed.json()["is_admin"], "a registrant is never an administrator")

            client.post("/web/logout")
            self.login(client, "admin", "adminpass123")
            client.patch("/web/admin/settings", json={"registration_open": False})
            rejected = client.post(
                "/web/register", json={"username": "carol", "password": "password123"}
            )
            self.assertEqual(rejected.status_code, 403)

    def test_login_rejects_a_bad_password(self):
        with self.make_client(admin_password="adminpass123") as client:
            bad = client.post("/web/login", json={"username": "admin", "password": "wrong"})
            self.assertEqual(bad.status_code, 401)

    def test_weak_credentials_are_rejected(self):
        with self.make_client() as client:
            self.assertEqual(
                client.post("/web/register", json={"username": "ab", "password": "password123"}).status_code,
                422,
            )
            self.assertEqual(
                client.post("/web/register", json={"username": "alice", "password": "short"}).status_code,
                422,
            )

    def test_duplicate_username_is_rejected(self):
        with self.make_client() as client:
            client.post("/web/register", json={"username": "alice", "password": "password123"})
            # alice is the administrator now; open registration so the clash is reached
            client.patch("/web/admin/settings", json={"registration_open": True})
            again = client.post("/web/register", json={"username": "alice", "password": "password123"})
            self.assertEqual(again.status_code, 409)


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def make_client(self, **overrides):
        return TestClient(create_app(fake_settings(self.folder.name, **overrides)))

    def submit_search(self, client, query="example"):
        """Submit a search with the engine stubbed out and wait for the outcome."""
        with patch("annas_api.jobs.search_books", return_value=[]):
            response = client.post("/v1/search", json={"query": query})
            self.assertEqual(response.status_code, 202, response.text)
            job_id = response.json()["id"]
            for _ in range(200):
                job = client.get(f"/v1/jobs/{job_id}").json()
                if job["status"] in {"completed", "failed", "cancelled"}:
                    return job_id
                time.sleep(0.01)
        self.fail("job did not finish")

    def test_a_user_only_sees_their_own_jobs(self):
        with self.make_client(admin_password="adminpass123") as client:
            client.post("/web/login", json={"username": "admin", "password": "adminpass123"})
            client.post("/web/admin/users", json={"username": "bob", "password": "password123"})
            client.post("/web/admin/users", json={"username": "carol", "password": "password123"})
            client.post("/web/logout")

            client.post("/web/login", json={"username": "bob", "password": "password123"})
            bob_job = self.submit_search(client, "bob query")
            self.assertEqual(len(client.get("/v1/jobs").json()["jobs"]), 1)
            client.post("/web/logout")

            client.post("/web/login", json={"username": "carol", "password": "password123"})
            self.assertEqual(client.get("/v1/jobs").json()["jobs"], [])
            # Another user's job is indistinguishable from a missing one.
            self.assertEqual(client.get(f"/v1/jobs/{bob_job}").status_code, 404)
            self.assertEqual(client.post(f"/v1/jobs/{bob_job}/cancel").status_code, 404)
            client.post("/web/logout")

            client.post("/web/login", json={"username": "admin", "password": "adminpass123"})
            self.assertEqual(len(client.get("/v1/jobs").json()["jobs"]), 1)

    def test_the_env_token_is_an_administrator_and_sees_every_job(self):
        with self.make_client(api_token="env-token", admin_password="adminpass123") as client:
            client.post("/web/login", json={"username": "admin", "password": "adminpass123"})
            client.post("/web/admin/users", json={"username": "bob", "password": "password123"})
            client.post("/web/logout")

            client.post("/web/login", json={"username": "bob", "password": "password123"})
            bob_job = self.submit_search(client)

            headers = {"X-API-Key": "env-token"}
            self.assertEqual(len(client.get("/v1/jobs", headers=headers).json()["jobs"]), 1)
            self.assertEqual(client.get(f"/v1/jobs/{bob_job}", headers=headers).status_code, 200)


class ApiKeyAuthTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def make_client(self, **overrides):
        return TestClient(create_app(fake_settings(self.folder.name, **overrides)))

    def test_issued_keys_authenticate_and_can_be_revoked(self):
        with self.make_client(api_token=None, admin_password="adminpass123") as client:
            client.post("/web/login", json={"username": "admin", "password": "adminpass123"})
            created = client.post("/web/keys", json={"name": "ci"})
            self.assertEqual(created.status_code, 201, created.text)
            raw = created.json()["key"]
            key_id = created.json()["id"]
            self.assertTrue(raw.startswith("annas_"))
            # The secret is never readable again.
            self.assertNotIn("key", client.get("/web/keys").json()["keys"][0])
            client.post("/web/logout")

            self.assertEqual(client.get("/v1/jobs", headers={"X-API-Key": raw}).status_code, 200)

            client.post("/web/login", json={"username": "admin", "password": "adminpass123"})
            client.post(f"/web/keys/{key_id}/revoke")
            client.post("/web/logout")
            self.assertEqual(client.get("/v1/jobs", headers={"X-API-Key": raw}).status_code, 401)

    def test_key_remarks_can_be_edited_without_rotating_the_secret(self):
        with self.make_client(api_token=None, admin_password="adminpass123") as client:
            client.post("/web/login", json={"username": "admin", "password": "adminpass123"})
            created = client.post("/web/keys", json={"name": "old name"}).json()
            response = client.patch(f"/web/keys/{created['id']}", json={"name": "production"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(client.get("/web/keys").json()["keys"][0]["name"], "production")
            client.post("/web/logout")
            self.assertEqual(client.get("/v1/jobs", headers={"X-API-Key": created["key"]}).status_code, 200)

    def test_an_unknown_key_is_rejected(self):
        with self.make_client(api_token=None, admin_password="adminpass123") as client:
            self.assertEqual(
                client.get("/v1/jobs", headers={"X-API-Key": "annas_not-a-key"}).status_code, 401
            )
            self.assertEqual(client.get("/v1/jobs").status_code, 401)

    def test_disabling_a_user_revokes_their_keys_and_sessions(self):
        with self.make_client(api_token=None, admin_password="adminpass123") as client:
            client.post("/web/login", json={"username": "admin", "password": "adminpass123"})
            bob = client.post("/web/admin/users", json={"username": "bob", "password": "password123"}).json()
            bob_key = None
            client.post("/web/logout")

            client.post("/web/login", json={"username": "bob", "password": "password123"})
            bob_key = client.post("/web/keys", json={"name": "bob"}).json()["key"]
            self.assertEqual(client.get("/v1/jobs").status_code, 200)
            client.post("/web/logout")

            client.post("/web/login", json={"username": "admin", "password": "adminpass123"})
            client.patch(f"/web/admin/users/{bob['id']}", json={"is_active": False})
            client.post("/web/logout")

            self.assertEqual(client.get("/v1/jobs", headers={"X-API-Key": bob_key}).status_code, 401)
            self.assertEqual(
                client.post("/web/login", json={"username": "bob", "password": "password123"}).status_code,
                401,
            )

    def test_admin_can_rename_and_reset_another_user(self):
        with self.make_client(api_token=None, admin_password="adminpass123") as client:
            client.post("/web/login", json={"username": "admin", "password": "adminpass123"})
            bob = client.post("/web/admin/users", json={"username": "bob", "password": "password123"}).json()
            changed = client.patch(f"/web/admin/users/{bob['id']}", json={
                "username": "robert", "password": "resetpass123",
            })
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertEqual(changed.json()["username"], "robert")
            client.post("/web/logout")
            self.assertEqual(client.post("/web/login", json={"username": "bob", "password": "password123"}).status_code, 401)
            self.assertEqual(client.post("/web/login", json={"username": "robert", "password": "resetpass123"}).status_code, 200)

    def test_last_enabled_admin_cannot_be_demoted_or_deleted(self):
        with self.make_client(api_token=None, admin_password="adminpass123") as client:
            client.post("/web/login", json={"username": "admin", "password": "adminpass123"})
            admin = client.get("/web/me").json()
            self.assertEqual(client.patch(f"/web/admin/users/{admin['id']}", json={"is_admin": False}).status_code, 409)
            self.assertEqual(client.patch(f"/web/admin/users/{admin['id']}", json={"is_active": False}).status_code, 409)


class AdminGuardTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def make_client(self, **overrides):
        return TestClient(create_app(fake_settings(self.folder.name, **overrides)))

    def test_admin_routes_require_an_administrator(self):
        with self.make_client(admin_password="adminpass123", registration_open=True) as client:
            client.post("/web/register", json={"username": "bob", "password": "password123"})
            self.assertEqual(client.get("/web/admin/users").status_code, 403)
            self.assertEqual(client.get("/web/admin/settings").status_code, 403)
            self.assertEqual(
                client.patch("/web/admin/settings", json={"registration_open": False}).status_code, 403
            )

    def test_anonymous_callers_are_rejected_once_accounts_exist(self):
        with self.make_client(admin_password="adminpass123") as client:
            self.assertEqual(client.get("/web/me").status_code, 401)
            self.assertEqual(client.get("/v1/jobs").status_code, 401)

    def test_healthz_and_the_console_stay_public(self):
        with self.make_client(admin_password="adminpass123") as client:
            self.assertEqual(client.get("/healthz").json(), {"status": "ok"})
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/static/app.js").status_code, 200)
