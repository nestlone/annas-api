"""SQLite access and schema for annas-api.

Everything lives in one database file on purpose: a quota check and the job row
it guards must be written in a single transaction, so they cannot share an
interpreter lock across two files.
"""

import sqlite3

SCHEMA_VERSION = 1

# `jobs` is created here rather than in jobs.py so a fresh database and a
# migrated one end up identical. Existing deployments already have the table,
# so `CREATE TABLE IF NOT EXISTS` is a no-op for them and `_add_missing_columns`
# takes care of the additive `owner_id`.
_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        result_json TEXT,
        file_path TEXT,
        error TEXT,
        owner_id INTEGER,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT,
        is_admin INTEGER NOT NULL DEFAULT 0,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        key_prefix TEXT NOT NULL,
        key_hash TEXT NOT NULL UNIQUE,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL,
        last_used_at INTEGER,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quotas (
        user_id INTEGER PRIMARY KEY,
        daily_searches INTEGER NOT NULL DEFAULT 0,
        daily_downloads INTEGER NOT NULL DEFAULT 0,
        max_concurrent_jobs INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS usage_counters (
        user_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        searches INTEGER NOT NULL DEFAULT 0,
        downloads INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, day),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
)

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_id)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_owner_status ON jobs(owner_id, status)",
)

# Additive columns for databases created before they existed.
_ADDED_COLUMNS = {
    "jobs": {"owner_id": "ALTER TABLE jobs ADD COLUMN owner_id INTEGER"},
}


def connect(path):
    """Open a connection configured for concurrent readers and one writer."""
    connection = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    # WAL lets readers proceed while a writer holds the lock; busy_timeout makes
    # a contended writer wait instead of raising "database is locked".
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA synchronous = NORMAL")
    # Required for the ON DELETE CASCADE rules on the account tables.
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def migrate(connection):
    """Create anything missing. Safe to run on every start and on a live database."""
    for statement in _TABLES:
        connection.execute(statement)
    # Indexes may reference columns that only the ALTER statements below add, so
    # the columns have to exist first.
    _add_missing_columns(connection)
    for statement in _INDEXES:
        connection.execute(statement)
    connection.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)


def _add_missing_columns(connection):
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(%s)" % table)}
        if not existing:
            continue
        for name, statement in columns.items():
            if name not in existing:
                connection.execute(statement)
