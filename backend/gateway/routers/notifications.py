from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.communications.corevia_email.service import (
    load_config as load_email_config,
    save_config as save_email_config,
    send_email,
)
from backend.gateway.admin_auth import require_super_admin_request
from backend.gateway.permissions_store import _api_policy

router = APIRouter()


def _ensure_email_online() -> None:
    pol = _api_policy("email")
    if not bool(pol.get("enabled")):
        raise HTTPException(status_code=503, detail="Email API is disabled by admin permissions.")
    if not bool(pol.get("is_online_now")):
        raise HTTPException(status_code=503, detail="Email API is currently offline (manual/scheduled mode).")


def _api_error(exc: Exception, default_status: int = 400) -> JSONResponse:
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=int(exc.status_code),
            content={"status": "error", "error": str(exc.detail or "Request failed.")},
        )
    return JSONResponse(status_code=default_status, content={"status": "error", "error": str(exc)})


class EmailRequest(BaseModel):
    to_email: str = Field(..., min_length=3)
    subject: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)


class EmailTestRequest(BaseModel):
    to_email: str = Field(..., min_length=3)
    subject: str = "Tulip Bookings test email"
    body: str = "Email configuration test successful."


@router.post("/api/notify/email")
def notify_email(req: EmailRequest):
    try:
        _ensure_email_online()
    except Exception as e:
        return _api_error(e, default_status=503)
    ok, msg = send_email(req.to_email, req.subject, req.body)
    if ok:
        return {"status": "ok"}
    return JSONResponse(status_code=500, content={"status": "error", "error": msg})


@router.get("/api/other-apis/email")
def email_config_get(request: Request):
    require_super_admin_request(request)
    try:
        return load_email_config()
    except Exception as e:
        return _api_error(e, default_status=400)


@router.post("/api/other-apis/email")
def email_config_set(request: Request, payload: dict):
    require_super_admin_request(request)
    try:
        return save_email_config(payload or {})
    except Exception as e:
        return _api_error(e, default_status=400)


@router.post("/api/other-apis/email/test-send")
def email_test_send(request: Request, req: EmailTestRequest):
    require_super_admin_request(request)
    try:
        _ensure_email_online()
    except Exception as e:
        return _api_error(e, default_status=503)
    ok, msg = send_email(req.to_email, req.subject, req.body)
    if ok:
        return {"status": "ok", "message": "Test email sent."}
    return JSONResponse(status_code=500, content={"status": "error", "error": msg})
