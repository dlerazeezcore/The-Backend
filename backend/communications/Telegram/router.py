from __future__ import annotations

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from backend.auth.api import get_authenticated_user, read_request_payload
from backend.gateway.admin_auth import require_super_admin_request
from backend.gateway.esim_app_store import get_user_by_id, upsert_push_device

from . import supabase_repo
from .schemas import ConversationResponse, CustomerMessageRequest
from .service import (
    ensure_telegram_webhook_registered,
    get_telegram_webhook_status,
    handle_telegram_update,
    load_current_conversation,
    register_telegram_webhook,
    send_customer_message,
    validate_telegram_webhook_secret,
)


router = APIRouter()


def _pick_message_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


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


def _normalize_push_platform(value: object) -> str:
    platform = str(value or "").strip().lower()
    if platform in {"ios", "android", "web"}:
        return platform
    return "web"


def _read_push_enabled_header(value: object) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return True
    return text in {"1", "true", "yes", "on"}


def _sync_support_push_device_from_headers(request: Request, user: dict) -> None:
    install_id = str(request.headers.get("x-push-install-id") or "").strip()
    token = str(request.headers.get("x-push-token") or "").strip()
    if not install_id and not token:
        return

    user_id = str((user or {}).get("id") or "").strip()
    if not user_id:
        return

    try:
        upsert_push_device(
            {
                "installId": install_id,
                "token": token,
                "userId": user_id,
                "platform": _normalize_push_platform(request.headers.get("x-push-platform")),
                "notificationsEnabled": _read_push_enabled_header(request.headers.get("x-push-enabled")),
                "supportChatOpen": True,
            }
        )
    except Exception as exc:
        print(f"WARNING: support push device sync from headers failed: {exc}")


def _extract_support_push_target_from_headers(request: Request) -> dict[str, object]:
    install_id = str(request.headers.get("x-push-install-id") or "").strip()
    token = str(request.headers.get("x-push-token") or "").strip()
    if not install_id and not token:
        return {}

    target: dict[str, object] = {}
    if install_id:
        target["installId"] = install_id
    if token:
        target["token"] = token
    platform = _normalize_push_platform(request.headers.get("x-push-platform"))
    if platform:
        target["platform"] = platform
    target["notificationsEnabled"] = _read_push_enabled_header(request.headers.get("x-push-enabled"))
    return target


def _error_response(exc: Exception, default_status: int = 400) -> JSONResponse:
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=int(exc.status_code), content={"status": "error", "error": str(exc.detail)})
    return JSONResponse(status_code=default_status, content={"status": "error", "error": str(exc)})


@router.get("/api/telegram-support/conversation")
def get_support_conversation(request: Request):
    try:
        user = require_support_authenticated_user(request)
        _sync_support_push_device_from_headers(request, user)
        payload = load_current_conversation(user)
        response = ConversationResponse(**payload)
        return response.model_dump()
    except Exception as exc:
        return _error_response(exc, default_status=404)


@router.post("/api/telegram-support/messages")
async def create_support_message(
    request: Request,
    body: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
):
    try:
        user = require_support_authenticated_user(request)
        _sync_support_push_device_from_headers(request, user)
        support_push_target = _extract_support_push_target_from_headers(request)
        text = _pick_message_text(body)
        upload_meta = None
        content_type = str(request.headers.get("content-type") or "").lower()
        if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
            form = await request.form()
            text = _pick_message_text(
                text,
                form.get("body"),
                form.get("message"),
                form.get("text"),
                form.get("content"),
            )
        else:
            try:
                payload_json = await request.json()
            except Exception:
                payload_json = None
            if isinstance(payload_json, dict):
                text = _pick_message_text(
                    text,
                    payload_json.get("body"),
                    payload_json.get("message"),
                    payload_json.get("text"),
                    payload_json.get("content"),
                )
                if not text and file is None:
                    req = CustomerMessageRequest(**payload_json)
                    text = req.body
        if file is not None:
            file_content_type = str(file.content_type or "").strip().lower()
            if not file_content_type.startswith("image/"):
                raise HTTPException(status_code=400, detail="Only image attachments are supported.")
            blob = await file.read()
            if not blob:
                raise HTTPException(status_code=400, detail="Uploaded image is empty.")
            if len(blob) > 10 * 1024 * 1024:
                raise HTTPException(status_code=400, detail="Image attachment exceeds 10MB.")
            conversation = supabase_repo.get_or_create_open_conversation(user)
            conversation_id = str((conversation or {}).get("id") or "")
            upload_meta = supabase_repo.upload_attachment(
                conversation_id=conversation_id,
                filename=str(file.filename or "attachment"),
                content=blob,
                content_type=file_content_type,
            )
        payload = send_customer_message(
            user,
            text or "",
            attachment=upload_meta,
            support_push_target=support_push_target,
        )
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


@router.get("/api/telegram-support/admin/webhook")
def telegram_webhook_status(request: Request):
    try:
        require_super_admin_request(request)
        return {"status": "ok", "webhook": get_telegram_webhook_status()}
    except Exception as exc:
        return _error_response(exc, default_status=400)


@router.post("/api/telegram-support/admin/webhook/register")
async def telegram_webhook_register(request: Request):
    try:
        require_super_admin_request(request)
        payload = await read_request_payload(request)
        drop_pending_updates = bool((payload or {}).get("drop_pending_updates"))
        result = register_telegram_webhook(drop_pending_updates=drop_pending_updates)
        return {"status": "ok", "result": result}
    except Exception as exc:
        return _error_response(exc, default_status=400)


@router.post("/api/telegram-support/admin/webhook/ensure")
def telegram_webhook_ensure(request: Request):
    try:
        require_super_admin_request(request)
        return {"status": "ok", "result": ensure_telegram_webhook_registered()}
    except Exception as exc:
        return _error_response(exc, default_status=400)
