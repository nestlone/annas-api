"""FastAPI entry point for asynchronous search and verified download jobs."""

import hashlib
import hmac
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .jobs import JobService


class Settings:
    def __init__(self):
        self.data_dir = Path(os.environ.get("FERRY_API_DATA_DIR", "./ferry-data"))
        self.workers = max(1, min(int(os.environ.get("FERRY_API_WORKERS", "2")), 10))
        self.api_token = os.environ.get("FERRY_API_TOKEN")
        self.signing_key = os.environ.get("FERRY_API_SIGNING_KEY") or self.api_token or "development-only-change-me"
        self.file_url_ttl = max(60, min(int(os.environ.get("FERRY_API_FILE_URL_TTL", "900")), 86400))
        self.public_base_url = (os.environ.get("FERRY_API_PUBLIC_BASE_URL") or "").rstrip("/")
        retention_hours = max(1, min(int(os.environ.get("FERRY_API_FILE_RETENTION_HOURS", "24")), 8760))
        self.retention_seconds = retention_hours * 3600


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

    @asynccontextmanager
    async def lifespan(app):
        app.state.jobs = JobService(
            settings.data_dir, settings.workers, retention_seconds=settings.retention_seconds
        )
        yield
        app.state.jobs.close()

    app = FastAPI(title="annas-api", version="0.0.1", lifespan=lifespan)

    def require_token(x_api_key: Optional[str] = Header(default=None)):
        if settings.api_token and not hmac.compare_digest(x_api_key or "", settings.api_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的 API 密钥")

    def service(request: Request):
        return request.app.state.jobs

    def base_url(request):
        """Base for generated links; FERRY_API_PUBLIC_BASE_URL wins over the request.

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
        return response

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.post("/v1/search", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_token)])
    def submit_search(body: SearchRequest, request: Request, jobs=Depends(service)):
        try:
            job_id = jobs.submit_search(body.query, body.ext, body.limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": job_id, "status": "queued", "status_url": base_url(request) + f"/v1/jobs/{job_id}"}

    @app.post("/v1/downloads", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_token)])
    def submit_download(body: DownloadRequest, request: Request, jobs=Depends(service)):
        try:
            job_id = jobs.submit_download(body.md5, body.direct_url, body.name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": job_id, "status": "queued", "status_url": base_url(request) + f"/v1/jobs/{job_id}"}

    @app.get("/v1/jobs", dependencies=[Depends(require_token)])
    def list_jobs(
        request: Request,
        jobs=Depends(service),
        status: Optional[str] = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        try:
            rows = jobs.list_jobs(status=status, limit=limit, offset=offset)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        items = [job_summary(request, row) for row in rows]
        return {"jobs": items, "count": len(items), "limit": limit, "offset": offset}

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(require_token)])
    def get_job(job_id: str, request: Request, jobs=Depends(service)):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job_response(request, job)

    @app.post("/v1/jobs/{job_id}/cancel", dependencies=[Depends(require_token)])
    def cancel_job(job_id: str, request: Request, jobs=Depends(service)):
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

    return app


app = create_app()
