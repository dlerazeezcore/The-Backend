from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.auth.api import create_auth_router, require_authenticated_user
from backend.auth.service import is_super_admin
from backend.core.runtime import configure_cors, load_project_env

from .service import (
    load_config as load_email_config,
    save_config as save_email_config,
    send_email,
)


BUILD_ID = "email-backend-v1"
APP_DIR = Path(__file__).resolve().parent


class EmailRequest(BaseModel):
    to_email: str = Field(..., min_length=3)
    subject: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)


class EmailTestRequest(BaseModel):
    to_email: str = Field(..., min_length=3)
    subject: str = "Corevia Email test"
    body: str = "Email configuration test successful."


def _allow_public_signup() -> bool:
    return str(os.getenv("COREVIA_EMAIL_ALLOW_PUBLIC_SIGNUP") or "false").strip().lower() in {"1", "true", "yes", "on"}


def _allow_public_forgot_password() -> bool:
    return str(os.getenv("COREVIA_EMAIL_ALLOW_PUBLIC_FORGOT_PASSWORD") or "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _http_error(exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=int(exc.status_code), content={"detail": exc.detail})


def _email_api_enabled_for_user(user: dict[str, Any]) -> bool:
    if is_super_admin(user):
        return True
    api_access = user.get("api_access")
    if not isinstance(api_access, dict):
        return True
    return bool(api_access.get("email", True))


def _require_super_admin_request(request: Request) -> dict[str, Any]:
    current_user = require_authenticated_user(request)
    if not is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="Forbidden.")
    return current_user


def _mount_colocated_frontend(app: FastAPI) -> None:
    # If a frontend bundle is placed in this folder's frontend/dist
    # (or directly in frontend), serve it from the same process.
    candidates = [
        APP_DIR / "frontend" / "dist",
        APP_DIR / "frontend",
    ]
    for static_dir in candidates:
        if static_dir.exists() and (static_dir / "index.html").exists():
            app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="email-frontend")
            print(f"Email frontend mounted from: {static_dir}")
            return


load_project_env(__file__)
app = FastAPI(title="Corevia Email Backend", version="1.0.0")
configure_cors(app)
app.include_router(create_auth_router(), prefix="/api/auth")


@app.middleware("http")
async def _protect_email_routes(request: Request, call_next):
    path = request.url.path
    super_admin_paths = {
        "/api/other-apis/email",
        "/api/other-apis/email/test-send",
        "/api/email/config",
        "/api/email/test-send",
    }
    authenticated_send_paths = {
        "/api/notify/email",
        "/api/email/send",
    }

    try:
        if not _allow_public_signup() and path == "/api/auth/signup":
            raise HTTPException(status_code=403, detail="Public signup is disabled for this standalone service.")
        if not _allow_public_forgot_password() and path == "/api/auth/forgot-password":
            raise HTTPException(status_code=403, detail="Public password reset is disabled for this standalone service.")
        if path in super_admin_paths:
            _require_super_admin_request(request)
        elif path in authenticated_send_paths:
            current_user = require_authenticated_user(request)
            if not _email_api_enabled_for_user(current_user):
                raise HTTPException(status_code=403, detail="Email API is disabled for this account.")
    except HTTPException as exc:
        return _http_error(exc)

    return await call_next(request)


@app.get("/health")
async def health() -> dict[str, object]:
    try:
        cfg = load_email_config()
        accounts = cfg.get("accounts") if isinstance(cfg, dict) else []
        active_id = str((cfg or {}).get("active_account_id") or "").strip() if isinstance(cfg, dict) else ""
        return {
            "ok": True,
            "build": BUILD_ID,
            "service": "corevia_email",
            "accounts_count": len(accounts) if isinstance(accounts, list) else 0,
            "active_account_id": active_id,
        }
    except Exception as exc:
        return {"ok": False, "build": BUILD_ID, "service": "corevia_email", "error": str(exc)}


@app.get("/__build")
async def build() -> dict[str, str]:
    return {"build": BUILD_ID, "service": "corevia_email"}


@app.get("/api/other-apis/email")
def email_config_get() -> dict:
    return load_email_config()


@app.post("/api/other-apis/email")
def email_config_set(payload: dict) -> dict:
    return save_email_config(payload or {})


@app.post("/api/other-apis/email/test-send")
def email_test_send(req: EmailTestRequest) -> dict:
    ok, msg = send_email(req.to_email, req.subject, req.body)
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return {"status": "ok", "message": "Test email sent."}


@app.post("/api/notify/email")
def notify_email(req: EmailRequest) -> dict:
    ok, msg = send_email(req.to_email, req.subject, req.body)
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return {"status": "ok"}


# Optional modern aliases for frontend integrations that avoid legacy route names.
@app.get("/api/email/config")
def email_config_get_alias() -> dict:
    return email_config_get()


@app.post("/api/email/config")
def email_config_set_alias(payload: dict) -> dict:
    return email_config_set(payload)


@app.post("/api/email/test-send")
def email_test_send_alias(req: EmailTestRequest) -> dict:
    return email_test_send(req)


@app.post("/api/email/send")
def email_send_alias(req: EmailRequest) -> dict:
    return notify_email(req)


_mount_colocated_frontend(app)
