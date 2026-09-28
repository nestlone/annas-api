"""Web console: session auth, self-service keys and quota, administrator routes."""

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

SESSION_COOKIE = "ferry_session"
MAX_KEYS_PER_USER = 20
MIN_PASSWORD_LENGTH = 8
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def static_dir():
    return Path(__file__).resolve().parent / "static"


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class KeyRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=60)


class SettingsPatch(BaseModel):
    registration_open: Optional[bool] = None


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)
    is_admin: bool = False


class AdminUserPatch(BaseModel):
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=1, max_length=200)


class QuotaPatch(BaseModel):
    daily_searches: int = Field(ge=0)
    daily_downloads: int = Field(ge=0)
    max_concurrent_jobs: int = Field(ge=0)


def _clean_username(raw):
    username = (raw or "").strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(422, "用户名只能是 3-32 位的字母、数字、下划线、点或短横线")
    return username


def _clean_password(raw):
    if len(raw or "") < MIN_PASSWORD_LENGTH:
        raise HTTPException(422, "密码至少 {} 位".format(MIN_PASSWORD_LENGTH))
    return raw


def create_web_router(settings, accounts):
    ttl = getattr(settings, "session_ttl_seconds", 168 * 3600)
    secure = bool(getattr(settings, "session_secure", False))
    router = APIRouter(prefix="/web", include_in_schema=False)

    def start_session(response, user):
        raw = accounts.create_session(user["id"])
        response.set_cookie(
            SESSION_COOKIE,
            raw,
            max_age=ttl,
            httponly=True,
            samesite="lax",
            secure=secure,
            path="/",
        )
        return response

    def identity(request: Request):
        raw = request.cookies.get(SESSION_COOKIE)
        user = accounts.resolve_session(raw) if raw else None
        if user is None:
            raise HTTPException(401, "未登录或登录已失效")
        return user

    def admin(user=Depends(identity)):
        if not user["is_admin"]:
            raise HTTPException(403, "需要管理员权限")
        return user

    def public_user(user):
        return {"id": user["id"], "username": user["username"], "is_admin": user["is_admin"]}

    # ------------------------------------------------------------------ public

    @router.get("/settings")
    def public_settings():
        return {
            "setup_required": accounts.count_users() == 0,
            "registration_open": accounts.registration_open(),
            "min_password_length": MIN_PASSWORD_LENGTH,
        }

    @router.post("/register", status_code=201)
    def register(body: Credentials):
        setup = accounts.count_users() == 0
        if not setup and not accounts.registration_open():
            raise HTTPException(403, "注册未开放")
        username = _clean_username(body.username)
        password = _clean_password(body.password)
        try:
            # The very first account bootstraps the deployment and is an administrator.
            user_id = accounts.create_user(username, password, is_admin=setup)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        user = accounts.get_user(user_id)
        return start_session(JSONResponse(public_user(user), status_code=201), user)

    @router.post("/login")
    def login(body: Credentials):
        user = accounts.authenticate(body.username.strip(), body.password)
        if user is None:
            raise HTTPException(401, "用户名或密码错误")
        return start_session(JSONResponse(public_user(user)), user)

    @router.post("/logout")
    def logout(request: Request):
        accounts.delete_session(request.cookies.get(SESSION_COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @router.get("/me")
    def me(user=Depends(identity)):
        return {
            **public_user(user),
            "quota": accounts.get_quota(user["id"]),
            "usage": accounts.usage(user["id"]),
        }

    # ------------------------------------------------------------------ self service

    @router.get("/keys")
    def list_keys(user=Depends(identity)):
        return {"keys": accounts.list_keys(user["id"])}

    @router.post("/keys", status_code=201)
    def create_key(body: KeyRequest, user=Depends(identity)):
        if len(accounts.list_keys(user["id"])) >= MAX_KEYS_PER_USER:
            raise HTTPException(429, "密钥数量已达上限（{} 个），请先吊销不用的密钥".format(MAX_KEYS_PER_USER))
        name = (body.name or "").strip()[:60]
        created = accounts.create_key(user["id"], name)
        # `key` is the only time the secret is readable; only its hash is stored.
        return created

    @router.post("/keys/{key_id}/revoke")
    def revoke_key(key_id: int, user=Depends(identity)):
        if not accounts.revoke_key(user["id"], key_id):
            raise HTTPException(404, "密钥不存在")
        return {"ok": True}

    @router.get("/usage")
    def usage(user=Depends(identity)):
        return {"quota": accounts.get_quota(user["id"]), "usage": accounts.usage(user["id"])}

    # ------------------------------------------------------------------ admin

    @router.get("/admin/settings")
    def admin_settings(_admin=Depends(admin)):
        return {
            "registration_open": accounts.registration_open(),
            "user_count": accounts.count_users(),
        }

    @router.patch("/admin/settings")
    def update_settings(body: SettingsPatch, _admin=Depends(admin)):
        if body.registration_open is not None:
            accounts.set_registration_open(body.registration_open)
        return {"registration_open": accounts.registration_open()}

    @router.get("/admin/users")
    def list_users(
        _admin=Depends(admin),
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        users = accounts.list_users(limit=limit, offset=offset)
        return {"users": users, "count": accounts.count_users(), "limit": limit, "offset": offset}

    @router.post("/admin/users", status_code=201)
    def create_user(body: AdminUserCreate, _admin=Depends(admin)):
        username = _clean_username(body.username)
        password = _clean_password(body.password)
        try:
            user_id = accounts.create_user(username, password, is_admin=body.is_admin)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return accounts.get_user(user_id)

    @router.patch("/admin/users/{user_id}")
    def update_user(user_id: int, body: AdminUserPatch, caller=Depends(admin)):
        if accounts.get_user(user_id) is None:
            raise HTTPException(404, "用户不存在")
        if user_id == caller["id"] and body.is_active is False:
            raise HTTPException(409, "不能停用当前登录的账号")
        if body.is_active is not None:
            accounts.set_active(user_id, body.is_active)
        if body.is_admin is not None:
            accounts.set_admin(user_id, body.is_admin)
        if body.password:
            accounts.set_password(user_id, _clean_password(body.password))
        return accounts.get_user(user_id)

    @router.put("/admin/users/{user_id}/quota")
    def update_quota(user_id: int, body: QuotaPatch, _admin=Depends(admin)):
        if accounts.get_user(user_id) is None:
            raise HTTPException(404, "用户不存在")
        try:
            accounts.set_quota(
                user_id, body.daily_searches, body.daily_downloads, body.max_concurrent_jobs
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return accounts.get_quota(user_id)

    @router.post("/admin/users/{user_id}/usage/reset")
    def reset_usage(user_id: int, _admin=Depends(admin)):
        if accounts.get_user(user_id) is None:
            raise HTTPException(404, "用户不存在")
        accounts.reset_usage(user_id)
        return accounts.usage(user_id)

    @router.delete("/admin/users/{user_id}")
    def delete_user(user_id: int, caller=Depends(admin)):
        if user_id == caller["id"]:
            raise HTTPException(409, "不能删除当前登录的账号")
        if not accounts.delete_user(user_id):
            raise HTTPException(404, "用户不存在")
        return {"ok": True}

    return router
