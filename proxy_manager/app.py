from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import os
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .core import Core
from .service import Manager
from .store import Store
from .subscriptions import REGIONS

ROOT = Path(__file__).resolve().parent.parent


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310_000).hex()
    return salt + ":" + digest


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(Payload):
    password: str = Field(min_length=10, max_length=128)


def http_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
        parsed.port
    except ValueError:
        raise ValueError("请填写有效的 HTTP / HTTPS 链接；认证信息请通过订阅链接的参数提供") from None
    return value.strip()


class Subscription(Payload):
    name: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=1, max_length=4096)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("请填写订阅名称，不能包含换行或控制字符")
        return value

    @field_validator("url")
    @classmethod
    def check_url(cls, value):
        return http_url(value)


class Settings(Payload):
    selection_strategy: Literal["latency", "primary"] = "latency"
    primary_subscription_id: str | None = Field(default=None, max_length=64)
    excluded_regions: list[str] = Field(default_factory=list, max_length=30)
    exclude_keywords: str = Field(default="", max_length=2000)
    refresh_hours: int = Field(default=24, ge=1, le=720)
    test_url: str = Field(default="https://cp.cloudflare.com/generate_204", max_length=2048)
    test_interval: int = Field(default=300, ge=60, le=86400)
    proxy_port: int = Field(default=7891, ge=1024, le=65535)
    allow_lan: bool = False
    proxy_username: str = Field(default="", max_length=80)
    proxy_password: str = Field(default="", max_length=128)
    allowed_ips: list[str] = Field(default_factory=lambda: ["127.0.0.0/8", "::1/128"], max_length=100)

    @field_validator("excluded_regions")
    @classmethod
    def regions(cls, values):
        if any(v not in REGIONS for v in values):
            raise ValueError("包含不支持的地区")
        return list(dict.fromkeys(values))

    @field_validator("test_url")
    @classmethod
    def url(cls, value):
        return http_url(value)

    @field_validator("allowed_ips")
    @classmethod
    def networks(cls, values):
        try:
            return [str(ipaddress.ip_network(v.strip(), strict=False)) for v in values]
        except ValueError:
            raise ValueError("允许访问的 IP 请填写 IP 或网段，例如 192.168.1.0/24") from None

    @model_validator(mode="after")
    def credentials(self):
        if self.selection_strategy == "primary" and not self.primary_subscription_id:
            raise ValueError("请先选择主订阅")
        if any(c in self.proxy_username for c in ":\r\n") or any(c in self.proxy_password for c in "\r\n"):
            raise ValueError("代理账号不能包含冒号或换行，密码不能包含换行")
        if bool(self.proxy_username) != bool(self.proxy_password):
            raise ValueError("代理账号和密码需要同时填写")
        if self.allow_lan and (not self.proxy_username or len(self.proxy_password) < 10 or not self.allowed_ips):
            raise ValueError("允许其他机器连接时，请设置代理账号、至少 10 位密码和允许访问的 IP")
        return self


class Override(Payload):
    disabled: bool | None = None
    region: str | None = None

    @field_validator("region")
    @classmethod
    def check_region(cls, value):
        if value and value not in REGIONS:
            raise ValueError("地区不支持")
        return value


class Selection(Payload):
    node_id: str = Field(max_length=40)


class TestRequest(Payload):
    node_id: str | None = Field(default=None, max_length=40)


class RoutingSettings(Payload):
    strategy: Literal["latency", "primary"]
    primary_subscription_id: str | None = Field(default=None, max_length=64)


def create_app(data_dir: Path | None = None) -> FastAPI:
    directory = data_dir or Path(os.environ.get("PROXY_MANAGER_DATA", str(ROOT / "data")))
    store = Store(directory)
    core = Core(store.directory, store.state["controller_secret"], ROOT)
    manager = Manager(store, core)
    sessions: dict[str, float] = {}
    failures: dict[str, list[float]] = defaultdict(list)

    @asynccontextmanager
    async def lifespan(app):
        await manager.start()
        yield
        await manager.close()

    app = FastAPI(title="红嘴鸥代理服务", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.manager = manager

    @app.middleware("http")
    async def protection(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "请从管理页面内进行操作"}, status_code=403)
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 32_768:
                return JSONResponse({"detail": "请求内容过大"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        labels = {"password":"管理密码", "name":"订阅名称", "url":"订阅链接", "proxy_port":"代理端口",
                  "refresh_hours":"更新间隔", "test_interval":"检测间隔", "test_url":"检测地址"}
        messages = []
        for error in exc.errors():
            field = labels.get(str(error["loc"][-1]), "填写内容")
            kind, context = error["type"], error.get("ctx", {})
            if kind == "value_error":
                message = error["msg"].removeprefix("Value error, ")
            elif kind == "string_too_short":
                message = f"{field}至少需要 {context.get('min_length')} 个字符"
            elif kind == "string_too_long":
                message = f"{field}最多支持 {context.get('max_length')} 个字符"
            elif kind == "missing":
                message = f"请填写{field}"
            elif kind == "greater_than_equal":
                message = f"{field}不能小于 {context.get('ge')}"
            elif kind == "less_than_equal":
                message = f"{field}不能大于 {context.get('le')}"
            else:
                message = f"{field}格式不正确，请检查后重试"
            messages.append(message)
        return JSONResponse({"detail":"；".join(dict.fromkeys(messages))}, status_code=422)

    @app.exception_handler(OSError)
    async def storage_or_process_error(request, exc):
        return JSONResponse({"detail":"操作未完成，请检查数据目录权限、内核文件及端口后重试"}, status_code=500)

    def logged_in(request: Request):
        token = request.cookies.get("pm_session", "")
        key = hashlib.sha256(token.encode()).hexdigest()
        if sessions.get(key, 0) <= time.time():
            raise HTTPException(401, "请先登录")

    @app.get("/api/auth")
    async def auth_state(request: Request):
        token = request.cookies.get("pm_session", "")
        return {"initialized": bool(store.state["password_hash"]),
                "authenticated": sessions.get(hashlib.sha256(token.encode()).hexdigest(), 0) > time.time()}

    @app.post("/api/login")
    async def login(payload: Credentials, request: Request, response: Response):
        ip = request.client.host if request.client else "local"
        failures[ip] = [t for t in failures[ip] if t > time.time() - 300]
        if len(failures[ip]) >= 5:
            raise HTTPException(429, "尝试次数过多，请 5 分钟后再试")
        async with manager.lock:
            encoded = store.state["password_hash"]
            if encoded:
                candidate = await asyncio.to_thread(password_hash, payload.password, encoded.split(":")[0])
                if not hmac.compare_digest(candidate, encoded):
                    failures[ip].append(time.time())
                    raise HTTPException(401, "密码不正确")
            else:
                store.state["password_hash"] = await asyncio.to_thread(password_hash, payload.password)
                store.save()
                manager.event("管理密码已设置")
        failures.pop(ip, None)
        for key, expiry in list(sessions.items()):
            if expiry <= time.time():
                del sessions[key]
        token = secrets.token_urlsafe(32)
        sessions[hashlib.sha256(token.encode()).hexdigest()] = time.time() + 43200
        response.set_cookie("pm_session", token, max_age=43200, httponly=True,
                            secure=request.url.scheme == "https", samesite="strict")
        return {"ok": True}

    @app.post("/api/logout", dependencies=[Depends(logged_in)])
    async def logout(request: Request, response: Response):
        token = request.cookies.get("pm_session", "")
        sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
        response.delete_cookie("pm_session")
        return {"ok": True}

    @app.get("/api/state", dependencies=[Depends(logged_in)])
    async def state():
        return await manager.status()

    @app.post("/api/subscriptions", dependencies=[Depends(logged_in)])
    async def add_subscription(payload: Subscription):
        if len(store.state["subscriptions"]) >= 50:
            raise ValueError("最多支持 50 个订阅")
        await manager.save_subscription(payload.model_dump())
        return {"ok": True}

    @app.put("/api/subscriptions/{identifier}", dependencies=[Depends(logged_in)])
    async def update_subscription(identifier: str, payload: Subscription):
        await manager.save_subscription(payload.model_dump(), identifier)
        return {"ok": True}

    @app.delete("/api/subscriptions/{identifier}", dependencies=[Depends(logged_in)])
    async def delete_subscription(identifier: str):
        await manager.delete_subscription(identifier)
        return {"ok": True}

    @app.post("/api/subscriptions/refresh", dependencies=[Depends(logged_in)])
    async def refresh_all():
        return {"results": await manager.refresh()}

    @app.post("/api/subscriptions/{identifier}/refresh", dependencies=[Depends(logged_in)])
    async def refresh_one(identifier: str):
        return {"results": await manager.refresh([identifier])}

    @app.put("/api/settings", dependencies=[Depends(logged_in)])
    async def settings(payload: Settings):
        if payload.proxy_port in {core.port, int(os.environ.get("PROXY_MANAGER_PORT", "8787"))}:
            raise ValueError("代理端口不能与管理页面或内核控制端口相同")
        await manager.save_settings(payload.model_dump())
        return {"ok": True}

    @app.put("/api/routing", dependencies=[Depends(logged_in)])
    async def routing_settings(payload: RoutingSettings):
        await manager.save_routing(payload.strategy, payload.primary_subscription_id)
        return {"ok":True}

    @app.patch("/api/nodes/{identifier}", dependencies=[Depends(logged_in)])
    async def override_node(identifier: str, payload: Override):
        await manager.override(identifier, payload.model_dump(exclude_none=True))
        return {"ok": True}

    @app.post("/api/select", dependencies=[Depends(logged_in)])
    async def select_node(payload: Selection):
        await manager.select(payload.node_id)
        return {"ok": True}

    @app.post("/api/test", dependencies=[Depends(logged_in)])
    async def test_nodes(payload: TestRequest):
        await manager.start_tests(payload.node_id)
        return {"ok": True}

    @app.post("/api/core/restart", dependencies=[Depends(logged_in)])
    async def restart_core():
        async with manager.lock:
            await manager.cancel_tests()
            await core.stop()
            try:
                await core.apply(store.state)
                manager.applied, manager.error = True, ""
                manager.event("代理服务已重新启动")
            except (ValueError, OSError):
                manager.applied = False
                manager.error = "代理服务启动失败，请检查内核安装和端口"
                raise ValueError(manager.error) from None
        return {"ok": True}

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "static" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app
