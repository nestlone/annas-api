import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from annas_api import security
from annas_api.accounts import AccountStore, QuotaExceeded, utc_day


class PasswordTests(unittest.TestCase):
    def test_password_round_trip(self):
        stored = security.hash_password("hunter2hunter2")
        self.assertTrue(security.verify_password("hunter2hunter2", stored))
        self.assertFalse(security.verify_password("wrong-password", stored))

    def test_empty_or_unknown_hash_is_rejected(self):
        self.assertFalse(security.verify_password("x", None))
        self.assertFalse(security.verify_password("x", ""))
        self.assertFalse(security.verify_password("x", "md5$deadbeef"))

    def test_api_key_shape(self):
        raw, prefix, digest = security.generate_api_key()
        self.assertTrue(raw.startswith("fry_"))
        self.assertEqual(prefix, raw[:12])
        self.assertEqual(digest, security.hash_api_key(raw))
        self.assertEqual(len(security.generate_session_token()), len(raw) - len("fry_"))


class AccountStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = AccountStore(self.folder.name)
        self.store.migrate()

    def test_create_and_authenticate(self):
        self.store.create_user("alice", "password123")
        self.assertIsNotNone(self.store.authenticate("alice", "password123"))
        self.assertIsNone(self.store.authenticate("alice", "nope"))
        self.assertIsNone(self.store.authenticate("bob", "password123"))

    def test_duplicate_username_is_rejected(self):
        self.store.create_user("alice", "password123")
        with self.assertRaises(ValueError):
            self.store.create_user("alice", "password123")

    def test_inactive_user_cannot_authenticate(self):
        user_id = self.store.create_user("alice", "password123")
        self.store.set_active(user_id, False)
        self.assertIsNone(self.store.authenticate("alice", "password123"))

    def test_api_key_lifecycle(self):
        user_id = self.store.create_user("alice", "password123")
        created = self.store.create_key(user_id, "laptop")
        resolved = self.store.lookup_key(created["key"])
        self.assertEqual(resolved["id"], user_id)
        self.assertFalse(resolved["is_admin"])
        self.assertTrue(self.store.revoke_key(user_id, created["id"]))
        self.assertIsNone(self.store.lookup_key(created["key"]))

    def test_unknown_api_key_is_rejected(self):
        self.assertIsNone(self.store.lookup_key("fry_nope"))
        self.assertIsNone(self.store.lookup_key(None))

    def test_key_of_a_disabled_user_is_rejected(self):
        user_id = self.store.create_user("alice", "password123")
        created = self.store.create_key(user_id, "laptop")
        self.store.set_active(user_id, False)
        self.assertIsNone(self.store.lookup_key(created["key"]))

    def test_session_lifecycle(self):
        user_id = self.store.create_user("alice", "password123")
        raw = self.store.create_session(user_id)
        self.assertEqual(self.store.resolve_session(raw)["id"], user_id)
        self.assertTrue(self.store.delete_session(raw))
        self.assertIsNone(self.store.resolve_session(raw))

    def test_expired_session_is_rejected(self):
        user_id = self.store.create_user("alice", "password123")
        connection = self.store._connect()
        try:
            connection.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (security.hash_session_token("stale"), user_id, 0, int(time.time()) - 1),
            )
        finally:
            connection.close()
        self.assertIsNone(self.store.resolve_session("stale"))

    def test_env_token_is_idempotent_and_administrative(self):
        first = self.store.ensure_env_token("env-token", "api-token")
        second = self.store.ensure_env_token("env-token", "api-token")
        self.assertEqual(first, second)
        user = self.store.lookup_key("env-token")
        self.assertEqual(user["id"], first)
        self.assertTrue(user["is_admin"])
        connection = self.store._connect()
        try:
            count = connection.execute("SELECT COUNT(*) AS n FROM api_keys").fetchone()["n"]
        finally:
            connection.close()
        self.assertEqual(count, 1, "the env key must not be duplicated on restart")

    def test_rotating_the_env_token_drops_the_old_key(self):
        self.store.ensure_env_token("first-token")
        self.store.ensure_env_token("second-token")
        self.assertIsNone(self.store.lookup_key("first-token"))
        self.assertIsNotNone(self.store.lookup_key("second-token"))

    def test_bootstrap_admin_seeds_only_once(self):
        user_id = self.store.bootstrap_admin("admin", "password123")
        self.store.set_password(user_id, "changed123")
        self.assertEqual(self.store.bootstrap_admin("admin", "password123"), user_id)
        # The environment seeds the account; a password changed in the console wins.
        self.assertIsNotNone(self.store.authenticate("admin", "changed123"))
        self.assertIsNone(self.store.authenticate("admin", "password123"))

    def test_registration_setting_round_trip(self):
        self.assertFalse(self.store.registration_open())
        self.store.set_registration_open(True)
        self.assertTrue(self.store.registration_open())

    def test_delete_user_cascades_but_keeps_jobs(self):
        user_id = self.store.create_user("alice", "password123")
        self.store.create_key(user_id, "laptop")
        self.store.set_quota(user_id, 5, 5, 2)
        self.store.create_session(user_id)
        connection = self.store._connect()
        try:
            connection.execute(
                "INSERT INTO jobs (id, kind, status, payload_json, owner_id, created_at, updated_at) "
                "VALUES ('j1', 'search', 'completed', '{}', ?, 0, 0)",
                (user_id,),
            )
        finally:
            connection.close()

        self.assertTrue(self.store.delete_user(user_id))

        connection = self.store._connect()
        try:
            keys = connection.execute("SELECT COUNT(*) AS n FROM api_keys").fetchone()["n"]
            quotas = connection.execute("SELECT COUNT(*) AS n FROM quotas").fetchone()["n"]
            sessions = connection.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
            jobs = connection.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        finally:
            connection.close()
        self.assertEqual((keys, quotas, sessions), (0, 0, 0))
        self.assertEqual(jobs, 1, "job history must survive a user deletion")


_NO_OVERRIDE = object()


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = AccountStore(self.folder.name)
        self.store.migrate()
        self.user_id = self.store.create_user("alice", "password123")

    def reserve(self, kind="search", user_id=_NO_OVERRIDE):
        """Charge one unit the way JobService._submit does, in its own transaction."""
        target = self.user_id if user_id is _NO_OVERRIDE else user_id
        connection = self.store._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self.store.reserve(connection, target, kind)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        finally:
            connection.close()

    def test_zero_means_unlimited(self):
        for _ in range(5):
            self.reserve()
        self.assertEqual(self.store.usage(self.user_id)["searches"], 5)

    def test_daily_limits_are_tracked_per_kind(self):
        self.store.set_quota(self.user_id, 2, 1, 0)
        self.reserve("search")
        self.reserve("search")
        with self.assertRaises(QuotaExceeded):
            self.reserve("search")
        # A rejected submit must not consume anything.
        self.assertEqual(self.store.usage(self.user_id)["searches"], 2)

        # The two counters are independent.
        self.reserve("download")
        with self.assertRaises(QuotaExceeded):
            self.reserve("download")
        self.assertEqual(self.store.usage(self.user_id)["downloads"], 1)

    def test_concurrent_limit_counts_active_jobs(self):
        self.store.set_quota(self.user_id, 0, 0, 1)
        connection = self.store._connect()
        try:
            connection.execute(
                "INSERT INTO jobs (id, kind, status, payload_json, owner_id, created_at, updated_at) "
                "VALUES ('j1', 'search', 'queued', '{}', ?, 0, 0)",
                (self.user_id,),
            )
        finally:
            connection.close()
        with self.assertRaises(QuotaExceeded):
            self.reserve()

    def test_counters_reset_on_a_new_day(self):
        self.store.set_quota(self.user_id, 1, 0, 0)
        today = utc_day()
        tomorrow = utc_day(time.time() + 24 * 3600)
        connection = self.store._connect()
        try:
            connection.execute(
                "INSERT INTO usage_counters (user_id, day, searches, downloads) VALUES (?, ?, 1, 0)",
                (self.user_id, today),
            )
            connection.execute(
                "INSERT INTO usage_counters (user_id, day, searches, downloads) VALUES (?, ?, 1, 0)",
                (self.user_id, tomorrow),
            )
        finally:
            connection.close()
        # Today's counter is exhausted, so the charge is refused...
        with self.assertRaises(QuotaExceeded):
            self.reserve()
        # ...but the counter for the next day is untouched.
        self.assertEqual(self.store.usage(self.user_id, day=tomorrow)["searches"], 1)

    def test_reset_usage_clears_today(self):
        self.store.set_quota(self.user_id, 1, 0, 0)
        self.reserve()
        self.store.reset_usage(self.user_id)
        self.assertEqual(self.store.usage(self.user_id)["searches"], 0)
        self.reserve()  # no longer refused

    def test_anonymous_callers_are_never_limited(self):
        self.store.set_quota(self.user_id, 1, 1, 1)
        for _ in range(3):
            self.reserve(user_id=None)
        self.assertEqual(self.store.usage(self.user_id)["searches"], 0)

    def test_negative_quota_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.set_quota(self.user_id, -1, 0, 0)


class MigrationTests(unittest.TestCase):
    def test_migrate_upgrades_a_pre_accounts_database(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "jobs.sqlite3"

        # A database exactly as it looked before this feature existed.
        connection = sqlite3.connect(str(path))
        try:
            connection.execute(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, "
                "payload_json TEXT NOT NULL, result_json TEXT, file_path TEXT, error TEXT, "
                "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO jobs VALUES ('old', 'search', 'completed', '{}', NULL, NULL, NULL, 0, 0)"
            )
            connection.commit()
        finally:
            connection.close()

        store = AccountStore(folder.name)
        store.migrate()

        connection = store._connect()
        try:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
            row = connection.execute("SELECT owner_id FROM jobs WHERE id = 'old'").fetchone()
            tables = {
                item["name"]
                for item in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        finally:
            connection.close()
        self.assertIn("owner_id", columns)
        self.assertIsNone(row["owner_id"], "pre-existing rows must be kept, unowned")
        for name in ("users", "api_keys", "quotas", "usage_counters", "sessions", "app_settings"):
            self.assertIn(name, tables)

    def test_migrate_is_idempotent(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store = AccountStore(folder.name)
        store.migrate()
        store.create_user("alice", "password123")
        store.migrate()
        self.assertEqual(store.count_users(), 1)
