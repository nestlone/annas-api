"""Users, API keys, web sessions and per-user quotas.

All of it lives in the same SQLite file as the jobs, so `reserve()` can check a
quota and the caller can insert the job row in one transaction.
"""

import sqlite3
import time
from pathlib import Path

from . import db
from .security import (
    API_KEY_PREFIX_LENGTH,
    generate_api_key,
    generate_session_token,
    hash_api_key,
    hash_password,
    hash_session_token,
    verify_password,
)

DEFAULT_SESSION_TTL_SECONDS = 7 * 24 * 3600
REGISTRATION_OPEN = "registration_open"
ENV_TOKEN_NAME = "env:system"

QUOTA_COLUMNS = ("daily_searches", "daily_downloads", "max_concurrent_jobs")


class QuotaExceeded(Exception):
    """Raised when submitting would exceed one of the user's limits."""

    def __init__(self, kind, limit, scope="daily"):
        self.kind = kind
        self.limit = limit
        self.scope = scope
        label = "检索" if kind == "search" else "下载"
        if scope == "concurrent":
            message = "同时进行的任务已达上限（{} 个），请等待任务完成".format(limit)
        else:
            message = "今日{}次数已达上限（{} 次），请明天再试".format(label, limit)
        super().__init__(message)


def utc_day(now=None):
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else now))


class AccountStore:
    """Everything account-related, backed by ``<data_dir>/jobs.sqlite3``."""

    def __init__(self, data_dir, session_ttl_seconds=DEFAULT_SESSION_TTL_SECONDS):
        self.database = Path(data_dir) / "jobs.sqlite3"
        self.session_ttl_seconds = session_ttl_seconds

    def _connect(self):
        return db.connect(self.database)

    # ------------------------------------------------------------------ bootstrap

    def migrate(self):
        connection = self._connect()
        try:
            db.migrate(connection)
        finally:
            connection.close()

    def bootstrap_admin(self, username, password):
        """Create the administrator if missing.

        The environment only *seeds* the account: once it exists its password is
        left alone, so a password changed in the console is not reverted on the
        next restart.
        """
        if not username or not password:
            return None
        now = int(time.time())
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT id, password_hash FROM users WHERE username = ?", (username,)
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    "INSERT INTO users (username, password_hash, is_admin, is_active, "
                    "created_at, updated_at) VALUES (?, ?, 1, 1, ?, ?)",
                    (username, hash_password(password), now, now),
                )
                return cursor.lastrowid
            if row["password_hash"] is None:
                connection.execute(
                    "UPDATE users SET password_hash = ?, is_admin = 1, updated_at = ? WHERE id = ?",
                    (hash_password(password), now, row["id"]),
                )
            return row["id"]
        finally:
            connection.close()

    def ensure_env_token(self, token, username="api-token"):
        """Register ``ANNAS_API_TOKEN`` as an admin key for API clients."""
        if not token:
            return None
        key_hash = hash_api_key(token)
        now = int(time.time())
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            if row is None:
                user_id = connection.execute(
                    "INSERT INTO users (username, password_hash, is_admin, is_active, "
                    "created_at, updated_at) VALUES (?, NULL, 1, 1, ?, ?)",
                    (username, now, now),
                ).lastrowid
            else:
                user_id = row["id"]
                connection.execute(
                    "UPDATE users SET is_admin = 1, is_active = 1, updated_at = ? WHERE id = ?",
                    (now, user_id),
                )
            connection.execute("BEGIN IMMEDIATE")
            try:
                # Replace any previous env key, including one rotated in .env.
                connection.execute(
                    "DELETE FROM api_keys WHERE key_hash = ? OR (user_id = ? AND name = ?)",
                    (key_hash, user_id, ENV_TOKEN_NAME),
                )
                connection.execute(
                    "INSERT INTO api_keys (user_id, name, key_prefix, key_hash, is_active, created_at) "
                    "VALUES (?, ?, ?, ?, 1, ?)",
                    (user_id, ENV_TOKEN_NAME, token[:API_KEY_PREFIX_LENGTH], key_hash, now),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            return user_id
        finally:
            connection.close()

    # ------------------------------------------------------------------ settings

    def get_setting(self, key, default=None):
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        finally:
            connection.close()
        return default if row is None else row["value"]

    def set_setting(self, key, value):
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
        finally:
            connection.close()

    def registration_open(self, default=False):
        raw = self.get_setting(REGISTRATION_OPEN)
        return default if raw is None else raw == "1"

    def set_registration_open(self, opened):
        self.set_setting(REGISTRATION_OPEN, "1" if opened else "0")

    # ------------------------------------------------------------------ users

    def _user_dict(self, row):
        if row is None:
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "is_admin": bool(row["is_admin"]),
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
            "has_password": row["password_hash"] is not None,
        }

    def create_user(self, username, password, is_admin=False, is_active=True):
        now = int(time.time())
        connection = self._connect()
        try:
            cursor = connection.execute(
                "INSERT INTO users (username, password_hash, is_admin, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    username,
                    hash_password(password) if password else None,
                    1 if is_admin else 0,
                    1 if is_active else 0,
                    now,
                    now,
                ),
            )
            return cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            raise ValueError("用户名已存在") from exc
        finally:
            connection.close()

    def get_user(self, user_id):
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        finally:
            connection.close()
        return self._user_dict(row)

    def get_user_by_username(self, username):
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        finally:
            connection.close()
        return self._user_dict(row)

    def count_admins(self):
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT COUNT(*) AS n FROM users WHERE is_admin = 1 AND is_active = 1"
            ).fetchone()["n"]
        finally:
            connection.close()

    def count_users(self):
        connection = self._connect()
        try:
            return connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        finally:
            connection.close()

    def list_users(self, limit=100, offset=0, day=None):
        """Users with their quota and today's usage, for the admin console."""
        day = day or utc_day()
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT u.id, u.username, u.password_hash, u.is_admin, u.is_active, u.created_at,
                       COALESCE(q.daily_searches, 0) AS daily_searches,
                       COALESCE(q.daily_downloads, 0) AS daily_downloads,
                       COALESCE(q.max_concurrent_jobs, 0) AS max_concurrent_jobs,
                       COALESCE(c.searches, 0) AS used_searches,
                       COALESCE(c.downloads, 0) AS used_downloads
                FROM users u
                LEFT JOIN quotas q ON q.user_id = u.id
                LEFT JOIN usage_counters c ON c.user_id = u.id AND c.day = ?
                ORDER BY u.id
                LIMIT ? OFFSET ?
                """,
                (day, limit, offset),
            ).fetchall()
        finally:
            connection.close()
        users = []
        for row in rows:
            item = self._user_dict(row)
            item["quota"] = {
                "daily_searches": row["daily_searches"],
                "daily_downloads": row["daily_downloads"],
                "max_concurrent_jobs": row["max_concurrent_jobs"],
            }
            item["usage"] = {
                "day": day,
                "searches": row["used_searches"],
                "downloads": row["used_downloads"],
            }
            users.append(item)
        return users

    def set_active(self, user_id, active):
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?",
                (1 if active else 0, int(time.time()), user_id),
            )
            return cursor.rowcount == 1
        finally:
            connection.close()

    def set_admin(self, user_id, is_admin):
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE users SET is_admin = ?, updated_at = ? WHERE id = ?",
                (1 if is_admin else 0, int(time.time()), user_id),
            )
            return cursor.rowcount == 1
        finally:
            connection.close()

    def set_username(self, user_id, username):
        """Rename a user, keeping existing sessions and keys attached."""
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE users SET username = ?, updated_at = ? WHERE id = ?",
                (username, int(time.time()), user_id),
            )
            return cursor.rowcount == 1
        except sqlite3.IntegrityError as exc:
            raise ValueError("用户名已存在") from exc
        finally:
            connection.close()

    def set_password(self, user_id, password):
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(password), int(time.time()), user_id),
            )
            return cursor.rowcount == 1
        finally:
            connection.close()

    def delete_user(self, user_id):
        """Delete a user; keys, quota, counters and sessions cascade, jobs are kept."""
        connection = self._connect()
        try:
            cursor = connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return cursor.rowcount == 1
        finally:
            connection.close()

    # ------------------------------------------------------------------ api keys

    def create_key(self, user_id, name=""):
        raw, prefix, key_hash = generate_api_key()
        connection = self._connect()
        try:
            cursor = connection.execute(
                "INSERT INTO api_keys (user_id, name, key_prefix, key_hash, is_active, created_at) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (user_id, name, prefix, key_hash, int(time.time())),
            )
            return {"id": cursor.lastrowid, "key": raw, "prefix": prefix, "name": name}
        finally:
            connection.close()

    def list_keys(self, user_id):
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT id, name, key_prefix, is_active, created_at, last_used_at "
                "FROM api_keys WHERE user_id = ? ORDER BY id DESC",
                (user_id,),
            ).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def revoke_key(self, user_id, key_id):
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE api_keys SET is_active = 0 WHERE id = ? AND user_id = ?",
                (key_id, user_id),
            )
            return cursor.rowcount == 1
        finally:
            connection.close()

    def rename_key(self, user_id, key_id, name):
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE api_keys SET name = ? WHERE id = ? AND user_id = ?",
                (name, key_id, user_id),
            )
            return cursor.rowcount == 1
        finally:
            connection.close()

    def lookup_key(self, raw):
        """Resolve a presented API key to its owner, or None."""
        if not raw:
            return None
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT k.id AS key_id, k.user_id, u.username, u.is_admin, u.is_active "
                "FROM api_keys k JOIN users u ON u.id = k.user_id "
                "WHERE k.key_hash = ? AND k.is_active = 1",
                (hash_api_key(raw),),
            ).fetchone()
            if row is None or not row["is_active"]:
                return None
            connection.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (int(time.time()), row["key_id"]),
            )
        finally:
            connection.close()
        return {"id": row["user_id"], "username": row["username"], "is_admin": bool(row["is_admin"])}

    # ------------------------------------------------------------------ sessions

    def create_session(self, user_id):
        raw = generate_session_token()
        now = int(time.time())
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (hash_session_token(raw), user_id, now, now + self.session_ttl_seconds),
            )
        finally:
            connection.close()
        return raw

    def resolve_session(self, raw):
        if not raw:
            return None
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT s.user_id, s.expires_at, u.username, u.is_admin, u.is_active "
                "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                (hash_session_token(raw),),
            ).fetchone()
        finally:
            connection.close()
        if row is None or row["expires_at"] <= int(time.time()) or not row["is_active"]:
            return None
        return {"id": row["user_id"], "username": row["username"], "is_admin": bool(row["is_admin"])}

    def delete_session(self, raw):
        if not raw:
            return False
        connection = self._connect()
        try:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE token_hash = ?", (hash_session_token(raw),)
            )
            return cursor.rowcount == 1
        finally:
            connection.close()

    def delete_user_sessions(self, user_id):
        """Invalidate every browser session after a password or access change."""
        connection = self._connect()
        try:
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        finally:
            connection.close()

    def purge_expired_sessions(self, now=None):
        cutoff = int(time.time()) if now is None else now
        connection = self._connect()
        try:
            cursor = connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (cutoff,))
            return cursor.rowcount
        finally:
            connection.close()

    # ------------------------------------------------------------------ quotas

    def get_quota(self, user_id):
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT daily_searches, daily_downloads, max_concurrent_jobs "
                "FROM quotas WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return {name: 0 for name in QUOTA_COLUMNS}
        return {name: row[name] for name in QUOTA_COLUMNS}

    def set_quota(self, user_id, daily_searches, daily_downloads, max_concurrent_jobs):
        values = []
        for name, value in (
            ("daily_searches", daily_searches),
            ("daily_downloads", daily_downloads),
            ("max_concurrent_jobs", max_concurrent_jobs),
        ):
            if int(value) < 0:
                raise ValueError("{} 不能为负数".format(name))
            values.append(int(value))
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO quotas (user_id, daily_searches, daily_downloads, max_concurrent_jobs) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
                "daily_searches = excluded.daily_searches, "
                "daily_downloads = excluded.daily_downloads, "
                "max_concurrent_jobs = excluded.max_concurrent_jobs",
                (user_id, *values),
            )
        finally:
            connection.close()

    def usage(self, user_id, day=None):
        day = day or utc_day()
        connection = self._connect()
        try:
            counters = connection.execute(
                "SELECT searches, downloads FROM usage_counters WHERE user_id = ? AND day = ?",
                (user_id, day),
            ).fetchone()
            active = connection.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE owner_id = ? AND status IN ('queued', 'running')",
                (user_id,),
            ).fetchone()["n"]
        finally:
            connection.close()
        return {
            "day": day,
            "searches": counters["searches"] if counters else 0,
            "downloads": counters["downloads"] if counters else 0,
            "active_jobs": active,
        }

    def reset_usage(self, user_id, day=None):
        day = day or utc_day()
        connection = self._connect()
        try:
            connection.execute(
                "DELETE FROM usage_counters WHERE user_id = ? AND day = ?", (user_id, day)
            )
        finally:
            connection.close()

    def reserve(self, connection, user_id, kind, now=None):
        """Charge one unit of ``kind`` to ``user_id``, or raise ``QuotaExceeded``.

        Runs on the caller's connection so the check and the job insert share the
        caller's transaction; a concurrent submit therefore cannot slip past the
        same limit. ``user_id`` None (anonymous or the open API) is never limited.
        """
        if user_id is None:
            return
        column = "searches" if kind == "search" else "downloads"
        quota = connection.execute(
            "SELECT daily_searches, daily_downloads, max_concurrent_jobs "
            "FROM quotas WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        daily_limit = 0
        concurrent_limit = 0
        if quota is not None:
            daily_limit = quota["daily_searches"] if kind == "search" else quota["daily_downloads"]
            concurrent_limit = quota["max_concurrent_jobs"]

        day = utc_day(now)
        if daily_limit:
            row = connection.execute(
                "SELECT searches, downloads FROM usage_counters WHERE user_id = ? AND day = ?",
                (user_id, day),
            ).fetchone()
            if row is not None and row[column] >= daily_limit:
                raise QuotaExceeded(kind, daily_limit, "daily")

        if concurrent_limit:
            active = connection.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE owner_id = ? AND status IN ('queued', 'running')",
                (user_id,),
            ).fetchone()["n"]
            if active >= concurrent_limit:
                raise QuotaExceeded(kind, concurrent_limit, "concurrent")

        connection.execute(
            "INSERT INTO usage_counters (user_id, day, searches, downloads) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET {} = {} + 1".format(column, column),
            (user_id, day, 1 if column == "searches" else 0, 1 if column == "downloads" else 0),
        )

    # ------------------------------------------------------------------ login

    def authenticate(self, username, password):
        """Return the user dict when the credentials are valid, else None."""
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        finally:
            connection.close()
        if row is None or not row["is_active"] or not verify_password(password, row["password_hash"]):
            return None
        return self._user_dict(row)
