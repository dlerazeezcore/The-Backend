from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.auth.api import get_authenticated_user
from backend.gateway.esim_app_store import get_user_by_id

from .schemas import ConversationResponse, CustomerMessageRequest
from .service import (
    handle_telegram_update,
    load_current_conversation,
    send_customer_message,
    validate_telegram_webhook_secret,
)


router = APIRouter()


def _extract_local_user_id(request: Request) -> str:
    auth_header = str(request.headers.get("Authorization") or "").strip()
    if not auth_header.lower().startswith("bearer "):
        return ""
    token = auth_header.split(" ", 1)[1].strip()
    if token.startswith("local-") and len(token) > 6:
        return token[6:]
    return ""


def require_support_authenticated_user(request: Request) -> dict:
    user = get_authenticated_user(request)
    if user:
        return user

    local_user_id = _extract_local_user_id(request)
    if local_user_id:
        local_user = get_user_by_id(local_user_id)
        if local_user:
            return {
                "id": str(local_user.get("id") or ""),
                "username": str(local_user.get("name") or "User"),
                "email": "",
                "phone": str(local_user.get("phone") or ""),
                "first_name": str(local_user.get("name") or ""),
                "last_name": "",
                "company_name": "",
                "role": "esim_app_user",
            }

    raise HTTPException(status_code=401, detail="Unauthorized")


def _error_response(exc: Exception, default_status: int = 400) -> JSONResponse:
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=int(exc.status_code), content={"status": "error", "error": str(exc.detail)})
    return JSONResponse(status_code=default_status, content={"status": "error", "error": str(exc)})


@router.get("/api/telegram-support/conversation")
def get_support_conversation(request: Request):
    try:
        user = require_support_authenticated_user(request)
        payload = load_current_conversation(user)
        response = ConversationResponse(**payload)
        return response.model_dump()
    except Exception as exc:
        return _error_response(exc, default_status=404)


@router.post("/api/telegram-support/messages")
def create_support_message(request: Request, req: CustomerMessageRequest):
    try:
        user = require_support_authenticated_user(request)
        payload = send_customer_message(user, req.body)
        return {
            "status": "ok",
            "conversation": payload.get("conversation"),
            "message": payload.get("message"),
        }
    except Exception as exc:
        return _error_response(exc, default_status=400)


@router.post("/api/telegram-support/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if not validate_telegram_webhook_secret(x_telegram_bot_api_secret_token):
        return JSONResponse(status_code=401, content={"status": "error", "error": "Invalid Telegram webhook secret."})
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "error": "Invalid JSON payload."})
    try:
        result = handle_telegram_update(payload if isinstance(payload, dict) else {})
        return {"status": "ok", "result": result}
    except Exception as exc:
        return _error_response(exc, default_status=400)
