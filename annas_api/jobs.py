"""Durable background jobs for the HTTP API."""

import json
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import db
from .downloader import validate_md5, validate_public_https
from .engine import download_book, search_books

KNOWN_STATUSES = ("queued", "running", "completed", "failed", "cancelled")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
DEFAULT_RETENTION_SECONDS = 24 * 60 * 60
SWEEP_INTERVAL_SECONDS = 10 * 60


class JobService:
    """SQLite-backed jobs executed by a bounded local worker pool."""

    def __init__(self, data_dir, workers=2, retention_seconds=DEFAULT_RETENTION_SECONDS):
        self.data_dir = Path(data_dir).resolve()
        self.download_dir = self.data_dir / "downloads"
        self.database = self.data_dir / "jobs.sqlite3"
        self.retention_seconds = retention_seconds
        # Assigned by create_app once the account store exists; None disables
        # quota enforcement entirely (anonymous / single-token deployments).
        self.quota = None
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ferry-job")
        self._futures = {}
        self._futures_lock = threading.Lock()
        self._stop = threading.Event()
        self._initialize()
        self._sweeper = threading.Thread(
            target=self._sweep_loop, name="ferry-retention", daemon=True
        )
        self._sweeper.start()

    def _connect(self):
        return db.connect(self.database)

    def _initialize(self):
        connection = self._connect()
        try:
            db.migrate(connection)
            # Work cannot safely resume after a process restart because an in-memory
            # future no longer exists. Preserve the row and make this explicit.
            connection.execute(
                "UPDATE jobs SET status = 'failed', error = ?, updated_at = ? "
                "WHERE status IN ('queued', 'running')",
                ("服务重启前任务未完成；请重新提交。", int(time.time())),
            )
        finally:
            connection.close()

    def close(self):
        self._stop.set()
        self._sweeper.join(timeout=5)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _sweep_loop(self):
        while True:
            try:
                self.purge_expired()
            except Exception:
                # A transient failure (e.g. a locked database) must not kill the
                # sweeper; the next tick retries.
                pass
            if self._stop.wait(SWEEP_INTERVAL_SECONDS):
                return

    def purge_expired(self, now=None):
        """Delete finished jobs and their delivered files past the retention window.

        Only terminal rows are eligible. Queued and running jobs have no file yet
        and may still be mutated by a worker, so they are never touched. Files go
        first: if the row delete then fails, ``completed_file`` still rejects the
        missing path and the next sweep retries the row.
        """
        cutoff = (int(time.time()) if now is None else now) - self.retention_seconds
        placeholders = ", ".join("?" * len(TERMINAL_STATUSES))
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT id FROM jobs WHERE status IN ({placeholders}) AND updated_at < ?",
                (*TERMINAL_STATUSES, cutoff),
            ).fetchall()
        finally:
            connection.close()

        expired = [row["id"] for row in rows]
        if not expired:
            return 0
        for job_id in expired:
            shutil.rmtree(self.download_dir / job_id, ignore_errors=True)

        connection = self._connect()
        try:
            connection.executemany(
                "DELETE FROM jobs WHERE id = ?", [(job_id,) for job_id in expired]
            )
            connection.commit()
        finally:
            connection.close()
        return len(expired)

    def submit_search(self, query, ext=None, limit=10, owner_id=None):
        if not query or not query.strip():
            raise ValueError("检索关键词不能为空")
        if not 1 <= limit <= 50:
            raise ValueError("limit 必须在 1 到 50 之间")
        return self._submit(
            "search", {"query": query.strip(), "ext": ext, "limit": limit}, owner_id=owner_id
        )

    def submit_download(self, md5=None, direct_url=None, name=None, owner_id=None):
        if bool(md5) == bool(direct_url):
            raise ValueError("必须且只能提供 md5 或 direct_url")
        if md5:
            validate_md5(md5)
        if direct_url:
            validate_public_https(direct_url)
        return self._submit(
            "download",
            {"md5": md5, "direct_url": direct_url, "name": name},
            owner_id=owner_id,
        )

    def _submit(self, kind, payload, owner_id=None):
        job_id = uuid.uuid4().hex
        now = int(time.time())
        connection = self._connect()
        try:
            # The write lock is taken before the quota check so the check and the
            # insert below cannot interleave with a competing submit; two requests
            # can never both pass the same limit.
            connection.execute("BEGIN IMMEDIATE")
            try:
                if self.quota is not None:
                    self.quota.reserve(connection, owner_id, kind)
                connection.execute(
                    "INSERT INTO jobs (id, kind, status, payload_json, owner_id, created_at, updated_at) "
                    "VALUES (?, ?, 'queued', ?, ?, ?, ?)",
                    (job_id, kind, json.dumps(payload, ensure_ascii=False), owner_id, now, now),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        finally:
            connection.close()
        future = self._executor.submit(self._run, job_id)
        with self._futures_lock:
            self._futures[job_id] = future
        # A free worker can finish before the line above runs, so prune from the
        # callback (which also fires immediately for an already-done future)
        # rather than from inside _run.
        future.add_done_callback(lambda _future, jid=job_id: self._forget(jid))
        return job_id

    def _forget(self, job_id):
        with self._futures_lock:
            self._futures.pop(job_id, None)

    def _start(self, job_id):
        """Claim a queued job for execution, returning False if someone else won."""
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ? AND status = 'queued'",
                (int(time.time()), job_id),
            )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            connection.close()

    def _set(self, job_id, status, result=None, file_path=None, error=None):
        """Record a terminal outcome for a job this worker started.

        The `status = 'running'` guard is deliberate: only the thread that won
        `_start` may finish the job, so a cancelled row can never be revived.
        """
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE jobs SET status = ?, result_json = ?, file_path = ?, error = ?, updated_at = ? "
                "WHERE id = ? AND status = 'running'",
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    str(file_path) if file_path else None,
                    error,
                    int(time.time()),
                    job_id,
                ),
            )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            connection.close()

    def _run(self, job_id):
        try:
            job = self.get(job_id, include_payload=True)
            if not job:
                return
            if not self._start(job_id):
                return
            payload = job["payload"]
            if job["kind"] == "search":
                result = search_books(payload["query"], ext=payload.get("ext"), limit=payload["limit"], as_json=True)
                self._set(job_id, "completed", result=result)
                return

            target_dir = self.download_dir / job_id
            target_dir.mkdir(parents=True, exist_ok=True)
            result = Path(
                download_book(
                    md5=payload.get("md5"),
                    direct_url=payload.get("direct_url"),
                    output_dir=str(target_dir),
                    custom_filename=payload.get("name"),
                    quiet=True,
                )
            ).resolve()
            if target_dir not in result.parents:
                raise ValueError("下载器返回了任务目录外的文件")
            self._set(job_id, "completed", file_path=result)
        except Exception as exc:
            self._set(job_id, "failed", error=str(exc)[:1000])

    def cancel(self, job_id):
        """Cancel a job that has not started yet.

        Returns ``cancelled`` on success, otherwise ``running``, ``terminal`` or
        ``not_found`` so the caller can choose an HTTP status.
        """
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE jobs SET status = 'cancelled', updated_at = ? WHERE id = ? AND status = 'queued'",
                (int(time.time()), job_id),
            )
            connection.commit()
            won = cursor.rowcount == 1
        finally:
            connection.close()

        if won:
            with self._futures_lock:
                future = self._futures.pop(job_id, None)
            if future is not None:
                # Best effort: a worker may already have claimed the task, in
                # which case the guarded _start makes _run return immediately.
                future.cancel()
            return "cancelled"

        job = self.get(job_id)
        if job is None:
            return "not_found"
        return "running" if job["status"] == "running" else "terminal"

    def list_jobs(self, status=None, limit=50, offset=0, owner_id=None):
        """List jobs newest-first, optionally filtered by status and owner.

        ``owner_id`` None applies no ownership filter, which is what the system
        token and administrators want.
        """
        if status is not None and status not in KNOWN_STATUSES:
            raise ValueError("status 必须是 queued、running、completed、failed 或 cancelled 之一")
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间")
        if offset < 0:
            raise ValueError("offset 不能为负数")

        conditions = []
        params = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if owner_id is not None:
            conditions.append("owner_id = ?")
            params.append(owner_id)

        # result_json/payload_json are intentionally excluded: a page of search
        # results would dwarf the queue view.
        sql = "SELECT id, kind, status, file_path, error, created_at, updated_at FROM jobs"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        # created_at only has second resolution, so rowid breaks ties and keeps
        # pagination from repeating or skipping rows.
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        connection = self._connect()
        try:
            rows = connection.execute(sql, params).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def count_active(self, owner_id):
        """Number of queued or running jobs owned by ``owner_id``."""
        connection = self._connect()
        try:
            return connection.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE owner_id = ? AND status IN ('queued', 'running')",
                (owner_id,),
            ).fetchone()["n"]
        finally:
            connection.close()

    def get(self, job_id, include_payload=False):
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        data = dict(row)
        data["result"] = json.loads(data.pop("result_json")) if data.get("result_json") else None
        payload = json.loads(data.pop("payload_json"))
        if include_payload:
            data["payload"] = payload
        return data

    def completed_file(self, job_id):
        job = self.get(job_id)
        if not job or job["status"] != "completed" or not job.get("file_path"):
            return None
        path = Path(job["file_path"]).resolve()
        expected_parent = (self.download_dir / job_id).resolve()
        if expected_parent not in path.parents or not path.is_file():
            return None
        return path
