"""FastAPI entry point for asynchronous search and verified download jobs."""

import hashlib
import hmac
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .accounts import AccountStore, QuotaExceeded
from .browser import browser_runtime_error
from .jobs import BrowserUnavailable, JobService
from .web import SESSION_COOKIE, create_web_router, static_dir


def _env_flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self):
        self.data_dir = Path(os.environ.get("ANNAS_API_DATA_DIR", "./annas-api-data"))
        self.workers = max(1, min(int(os.environ.get("ANNAS_API_WORKERS", "2")), 10))
        self.api_token = os.environ.get("ANNAS_API_TOKEN")
        self.signing_key = os.environ.get("ANNAS_API_SIGNING_KEY") or self.api_token or "development-only-change-me"
        self.file_url_ttl = max(60, min(int(os.environ.get("ANNAS_API_FILE_URL_TTL", "900")), 86400))
        self.public_base_url = (os.environ.get("ANNAS_API_PUBLIC_BASE_URL") or "").rstrip("/")
        retention_hours = max(1, min(int(os.environ.get("ANNAS_API_FILE_RETENTION_HOURS", "24")), 8760))
        self.retention_seconds = retention_hours * 3600
        self.admin_username = os.environ.get("ANNAS_API_ADMIN_USERNAME", "admin")
        self.admin_password = os.environ.get("ANNAS_API_ADMIN_PASSWORD")
        self.token_username = os.environ.get("ANNAS_API_TOKEN_USERNAME", "api-token")
        self.registration_open = _env_flag("ANNAS_API_REGISTRATION_OPEN", False)
        session_hours = max(1, min(int(os.environ.get("ANNAS_API_SESSION_TTL_HOURS", "168")), 8760))
        self.session_ttl_seconds = session_hours * 3600
        # Cookies are marked Secure whenever the service is reached over HTTPS.
        self.session_secure = _env_flag(
            "ANNAS_API_SESSION_SECURE", self.public_base_url.startswith("https://")
        )
        self.browser_error = browser_runtime_error()


class Identity:
    """Who is calling: an account, or the anonymous caller of an open deployment."""

    def __init__(self, user_id=None, username=None, is_admin=False):
        self.id = user_id
        self.username = username
        self.is_admin = is_admin

    def can_see(self, job):
        if self.is_admin:
            return True
        return job.get("owner_id") == self.id


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    ext: Optional[str] = Field(default=None, max_length=20)
    limit: int = Field(default=10, ge=1, le=50)


class DownloadRequest(BaseModel):
    md5: Optional[str] = Field(default=None, min_length=32, max_length=32)
    direct_url: Optional[str] = Field(default=None, max_length=4096)
    name: Optional[str] = Field(default=None, max_length=180)


def create_app(settings=None):
    settings = settings or Settings()
    accounts = AccountStore(
        settings.data_dir,
        session_ttl_seconds=getattr(settings, "session_ttl_seconds", 168 * 3600),
    )

    @asynccontextmanager
    async def lifespan(app):
        jobs = JobService(
            settings.data_dir,
            settings.workers,
            retention_seconds=settings.retention_seconds,
            browser_error=getattr(settings, "browser_error", None),
        )
        accounts.migrate()
        accounts.bootstrap_admin(
            getattr(settings, "admin_username", "admin"), getattr(settings, "admin_password", None)
        )
        accounts.ensure_env_token(settings.api_token, getattr(settings, "token_username", "api-token"))
        # The environment only seeds the toggle; the console owns it afterwards.
        if accounts.get_setting("registration_open") is None:
            accounts.set_registration_open(getattr(settings, "registration_open", False))
        jobs.quota = accounts
        app.state.jobs = jobs
        app.state.accounts = accounts
        yield
        jobs.close()

    app = FastAPI(title="annas-api", version="0.0.1", lifespan=lifespan)

    def identity(request: Request, x_api_key: Optional[str] = Header(default=None)):
        store = request.app.state.accounts
        if x_api_key:
            user = store.lookup_key(x_api_key)
            if user is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的 API 密钥")
            return Identity(user["id"], user["username"], user["is_admin"])
        session = request.cookies.get(SESSION_COOKIE)
        if session:
            user = store.resolve_session(session)
            if user is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录")
            return Identity(user["id"], user["username"], user["is_admin"])
        # A deployment that never configured a token and has no accounts stays open,
        # matching the behaviour before accounts existed.
        if settings.api_token is None and store.count_users() == 0:
            return Identity()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 API 密钥")

    def service(request: Request):
        return request.app.state.jobs

    def base_url(request):
        """Base for generated links; ANNAS_API_PUBLIC_BASE_URL wins over the request.

        A reverse proxy that forwards its upstream address as the Host header
        would otherwise leak an unreachable internal URL to clients.
        """
        return settings.public_base_url or str(request.base_url).rstrip("/")

    def signed_download_url(request, job):
        expires = int(time.time()) + settings.file_url_ttl
        payload = f"{job['id']}:{expires}".encode("utf-8")
        signature = hmac.new(settings.signing_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        return f"{base_url(request)}/v1/files/{job['id']}?expires={expires}&signature={signature}"

    def job_response(request, job):
        response = {
            "id": job["id"],
            "kind": job["kind"],
            "status": job["status"],
            "result": job["result"],
            "error": job["error"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
        }
        if job["status"] == "completed" and job.get("file_path"):
            response["download_url"] = signed_download_url(request, job)
        elif job["status"] == "running" and job["kind"] == "download":
            response["transfer_bytes"] = app.state.jobs.transfer_bytes(job["id"])
        return response

    def job_summary(request, job):
        """Like job_response without the heavy `result`, matching list_jobs' projection."""
        response = {
            "id": job["id"],
            "kind": job["kind"],
            "status": job["status"],
            "error": job["error"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
        }
        if job["status"] == "completed" and job.get("file_path"):
            response["download_url"] = signed_download_url(request, job)
        elif job["status"] == "running" and job["kind"] == "download":
            response["transfer_bytes"] = app.state.jobs.transfer_bytes(job["id"])
        return response

    def library_item(request, job):
        return {
            "id": job["id"],
            "name": job["name"],
            "size_bytes": job["size_bytes"],
            "created_at": job["created_at"],
            "completed_at": job["updated_at"],
            "available_until": job["updated_at"] + settings.retention_seconds,
            "download_url": signed_download_url(request, job),
        }

    @app.get("/healthz")
    def health():
        if app.state.jobs.browser_error:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "degraded",
                    "browser": "unavailable",
                    "detail": app.state.jobs.browser_error,
                },
            )
        return {"status": "ok"}

    @app.post("/v1/search", status_code=status.HTTP_202_ACCEPTED)
    def submit_search(
        body: SearchRequest, request: Request, caller=Depends(identity), jobs=Depends(service)
    ):
        try:
            job_id = jobs.submit_search(body.query, body.ext, body.limit, owner_id=caller.id)
        except QuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except BrowserUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": job_id, "status": "queued", "status_url": base_url(request) + f"/v1/jobs/{job_id}"}

    @app.post("/v1/downloads", status_code=status.HTTP_202_ACCEPTED)
    def submit_download(
        body: DownloadRequest, request: Request, caller=Depends(identity), jobs=Depends(service)
    ):
        try:
            job_id = jobs.submit_download(
                body.md5, body.direct_url, body.name, owner_id=caller.id
            )
        except QuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except BrowserUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": job_id, "status": "queued", "status_url": base_url(request) + f"/v1/jobs/{job_id}"}

    @app.get("/v1/jobs")
    def list_jobs(
        request: Request,
        caller=Depends(identity),
        jobs=Depends(service),
        status: Optional[str] = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        try:
            rows = jobs.list_jobs(
                status=status, limit=limit, offset=offset,
                owner_id=None if caller.is_admin else caller.id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        items = [job_summary(request, row) for row in rows]
        return {"jobs": items, "count": len(items), "limit": limit, "offset": offset}

    @app.get("/v1/library")
    def library(request: Request, caller=Depends(identity), jobs=Depends(service)):
        """Completed downloads for the current account, retained for its file window."""
        items = [library_item(request, row) for row in jobs.list_library(caller.id)]
        return {
            "files": items,
            "retention_seconds": settings.retention_seconds,
            "count": len(items),
        }

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, request: Request, caller=Depends(identity), jobs=Depends(service)):
        job = jobs.get(job_id)
        if not job or not caller.can_see(job):
            raise HTTPException(status_code=404, detail="任务不存在")
        return job_response(request, job)

    @app.post("/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, request: Request, caller=Depends(identity), jobs=Depends(service)):
        job = jobs.get(job_id)
        if not job or not caller.can_see(job):
            raise HTTPException(status_code=404, detail="任务不存在")
        outcome = jobs.cancel(job_id)
        if outcome == "not_found":
            raise HTTPException(status_code=404, detail="任务不存在")
        if outcome == "running":
            raise HTTPException(status_code=409, detail="任务正在执行，无法取消；仅支持取消排队中的任务")
        if outcome == "terminal":
            raise HTTPException(status_code=409, detail="任务已结束，无法取消")
        return job_response(request, jobs.get(job_id))

    @app.get("/v1/files/{job_id}", name="download_file")
    def download_file(job_id: str, expires: int, signature: str, request: Request, jobs=Depends(service)):
        payload = f"{job_id}:{expires}".encode("utf-8")
        expected = hmac.new(settings.signing_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        if expires < int(time.time()) or not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=403, detail="下载链接无效或已过期")
        file_path = jobs.completed_file(job_id)
        if not file_path:
            raise HTTPException(status_code=404, detail="文件不存在或任务未完成")
        return FileResponse(str(file_path), filename=file_path.name, media_type="application/octet-stream")

    app.include_router(create_web_router(settings, accounts))
    app.mount("/static", StaticFiles(directory=str(static_dir())), name="static")

    @app.get("/", include_in_schema=False)
    def console():
        return FileResponse(str(static_dir() / "index.html"))

    return app


app = create_app()
