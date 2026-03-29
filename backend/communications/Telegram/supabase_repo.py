from __future__ import annotations

import mimetypes
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from .settings import read_float, read_text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _display_name(user: dict[str, Any] | None) -> str:
    row = user if isinstance(user, dict) else {}
    parts = [str(row.get("first_name") or "").strip(), str(row.get("last_name") or "").strip()]
    full_name = " ".join(part for part in parts if part).strip()
    if full_name:
        return full_name
    return (
        str(row.get("company_name") or "").strip()
        or str(row.get("username") or "").strip()
        or str(row.get("email") or "").strip()
        or str(row.get("phone") or "").strip()
        or "Customer"
    )


@dataclass(frozen=True)
class SupportSupabaseConfig:
    url: str
    key: str
    timeout_seconds: float
    conversations_table: str
    messages_table: str
    telegram_map_table: str
    attachments_bucket: str


def _config() -> SupportSupabaseConfig:
    return SupportSupabaseConfig(
        url=read_text("supabase_url").rstrip("/"),
        key=read_text("supabase_service_role_key"),
        timeout_seconds=read_float("supabase_timeout_seconds", 20.0),
        conversations_table=read_text("support_conversations_table", "support_conversations") or "support_conversations",
        messages_table=read_text("support_messages_table", "support_messages") or "support_messages",
        telegram_map_table=read_text("support_telegram_map_table", "support_telegram_map") or "support_telegram_map",
        attachments_bucket=read_text("support_attachments_bucket", "support-attachments") or "support-attachments",
    )


def _ensure_config(cfg: SupportSupabaseConfig) -> None:
    if not cfg.url or not cfg.key:
        raise RuntimeError("Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")


def _headers(cfg: SupportSupabaseConfig, *, prefer: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": cfg.key,
        "Authorization": f"Bearer {cfg.key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _endpoint(cfg: SupportSupabaseConfig, table: str) -> str:
    return f"{cfg.url}/rest/v1/{table}"


def _storage_object_endpoint(cfg: SupportSupabaseConfig, bucket: str, path: str) -> str:
    return f"{cfg.url}/storage/v1/object/{bucket}/{path.lstrip('/')}"


def _storage_public_url(cfg: SupportSupabaseConfig, bucket: str, path: str) -> str:
    return f"{cfg.url}/storage/v1/object/public/{bucket}/{path.lstrip('/')}"


def _storage_bucket_endpoint(cfg: SupportSupabaseConfig, bucket: str = "") -> str:
    suffix = f"/{bucket}" if bucket else ""
    return f"{cfg.url}/storage/v1/bucket{suffix}"


def _ensure_public_bucket(cfg: SupportSupabaseConfig, bucket: str) -> None:
    _ensure_config(cfg)
    response = requests.post(
        _storage_bucket_endpoint(cfg),
        headers=_headers(cfg),
        json={"id": bucket, "name": bucket, "public": True},
        timeout=cfg.timeout_seconds,
    )
    if response.status_code in {200, 201}:
        return
    if response.status_code == 409:
        return
    raise RuntimeError(f"Supabase storage bucket create failed ({response.status_code}): {response.text[:300]}")


def _is_bucket_missing_response(response: requests.Response) -> bool:
    text = str(getattr(response, "text", "") or "")
    lowered = text.lower()
    if "bucket not found" in lowered:
        return True
    if response.status_code == 404:
        return True
    return False


def _get_rows(
    cfg: SupportSupabaseConfig,
    table: str,
    *,
    params: dict[str, str],
) -> list[dict[str, Any]]:
    _ensure_config(cfg)
    response = requests.get(
        _endpoint(cfg, table),
        headers=_headers(cfg),
        params=params,
        timeout=cfg.timeout_seconds,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase select failed ({response.status_code}): {response.text[:300]}")
    payload = response.json()
    return payload if isinstance(payload, list) else []


def _insert_rows(cfg: SupportSupabaseConfig, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _ensure_config(cfg)
    response = requests.post(
        _endpoint(cfg, table),
        headers=_headers(cfg, prefer="return=representation"),
        json=rows,
        timeout=cfg.timeout_seconds,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase insert failed ({response.status_code}): {response.text[:300]}")
    payload = response.json()
    return payload if isinstance(payload, list) else []


def _patch_rows(
    cfg: SupportSupabaseConfig,
    table: str,
    *,
    match: dict[str, str],
    body: dict[str, Any],
) -> list[dict[str, Any]]:
    _ensure_config(cfg)
    response = requests.patch(
        _endpoint(cfg, table),
        headers=_headers(cfg, prefer="return=representation"),
        params=match,
        json=body,
        timeout=cfg.timeout_seconds,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase update failed ({response.status_code}): {response.text[:300]}")
    payload = response.json()
    return payload if isinstance(payload, list) else []


def get_or_create_open_conversation(user: dict[str, Any]) -> dict[str, Any]:
    cfg = _config()
    customer_user_id = _clean_text(user.get("id"))
    if not customer_user_id:
        raise RuntimeError("Authenticated user is missing an id.")

    rows = _get_rows(
        cfg,
        cfg.conversations_table,
        params={
            "select": "*",
            "customer_user_id": f"eq.{customer_user_id}",
            "status": "eq.open",
            "order": "last_message_at.desc",
            "limit": "1",
        },
    )
    if rows:
        return rows[0]

    now = _now_iso()
    created = _insert_rows(
        cfg,
        cfg.conversations_table,
        [
            {
                "id": str(uuid.uuid4()),
                "customer_user_id": customer_user_id,
                "customer_display_name": _display_name(user),
                "status": "open",
                "source": "in_app",
                "created_at": now,
                "updated_at": now,
                "last_message_at": now,
                "latest_customer_message_preview": "",
            }
        ],
    )
    if not created:
        raise RuntimeError("Failed to create support conversation.")
    return created[0]


def touch_conversation(
    conversation_id: str,
    *,
    latest_customer_message_preview: str | None = None,
    telegram_chat_id: str | None = None,
    telegram_thread_id: int | None = None,
) -> dict[str, Any]:
    cfg = _config()
    now = _now_iso()
    body: dict[str, Any] = {
        "updated_at": now,
        "last_message_at": now,
    }
    if latest_customer_message_preview is not None:
        body["latest_customer_message_preview"] = str(latest_customer_message_preview or "")[:500]
    if telegram_chat_id is not None:
        body["telegram_chat_id"] = telegram_chat_id
    if telegram_thread_id is not None:
        body["telegram_thread_id"] = telegram_thread_id
    updated = _patch_rows(
        cfg,
        cfg.conversations_table,
        match={"id": f"eq.{conversation_id}"},
        body=body,
    )
    if not updated:
        raise RuntimeError("Conversation update did not return a row.")
    return updated[0]


def create_message(
    *,
    conversation_id: str,
    sender_type: str,
    sender_user_id: str = "",
    sender_display_name: str = "",
    body: str,
    telegram_chat_id: str | None = None,
    telegram_message_id: int | None = None,
    reply_to_telegram_message_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _config()
    created = _insert_rows(
        cfg,
        cfg.messages_table,
        [
            {
                "id": str(uuid.uuid4()),
                "conversation_id": conversation_id,
                "sender_type": sender_type,
                "sender_user_id": sender_user_id,
                "sender_display_name": sender_display_name,
                "body": body,
                "telegram_chat_id": telegram_chat_id,
                "telegram_message_id": telegram_message_id,
                "reply_to_telegram_message_id": reply_to_telegram_message_id,
                "metadata": metadata or {},
                "created_at": _now_iso(),
            }
        ],
    )
    if not created:
        raise RuntimeError("Failed to create support message.")
    return created[0]


def upload_attachment(
    *,
    conversation_id: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> dict[str, Any]:
    cfg = _config()
    _ensure_config(cfg)
    safe_name = (filename or "attachment").strip().replace("\\", "_").replace("/", "_")
    ext = ""
    if "." in safe_name:
        ext = "." + safe_name.rsplit(".", 1)[-1].lower()
    if not ext:
        guessed = mimetypes.guess_extension(content_type or "") or ""
        ext = guessed if isinstance(guessed, str) else ""
    path = f"support/{conversation_id}/{uuid.uuid4().hex}{ext}"
    headers = {
        "apikey": cfg.key,
        "Authorization": f"Bearer {cfg.key}",
        "Content-Type": content_type or "application/octet-stream",
        "x-upsert": "false",
    }
    response = requests.post(
        _storage_object_endpoint(cfg, cfg.attachments_bucket, path),
        headers=headers,
        data=content,
        timeout=cfg.timeout_seconds,
    )
    if _is_bucket_missing_response(response):
        _ensure_public_bucket(cfg, cfg.attachments_bucket)
        response = requests.post(
            _storage_object_endpoint(cfg, cfg.attachments_bucket, path),
            headers=headers,
            data=content,
            timeout=cfg.timeout_seconds,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase storage upload failed ({response.status_code}): {response.text[:300]}")
    return {
        "bucket": cfg.attachments_bucket,
        "path": path,
        "url": _storage_public_url(cfg, cfg.attachments_bucket, path),
        "name": safe_name,
        "mimeType": content_type or "application/octet-stream",
        "content_type": content_type or "application/octet-stream",
        "size": len(content),
    }


def list_messages(conversation_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    cfg = _config()
    return _get_rows(
        cfg,
        cfg.messages_table,
        params={
            "select": "*",
            "conversation_id": f"eq.{conversation_id}",
            "order": "created_at.asc",
            "limit": str(max(1, min(limit, 500))),
        },
    )


def store_telegram_map(
    *,
    conversation_id: str,
    app_message_id: str,
    telegram_chat_id: str,
    telegram_message_id: int,
    telegram_thread_id: int | None,
    direction: str,
) -> dict[str, Any]:
    cfg = _config()
    created = _insert_rows(
        cfg,
        cfg.telegram_map_table,
        [
            {
                "id": str(uuid.uuid4()),
                "conversation_id": conversation_id,
                "app_message_id": app_message_id,
                "telegram_chat_id": telegram_chat_id,
                "telegram_message_id": telegram_message_id,
                "telegram_thread_id": telegram_thread_id,
                "direction": direction,
                "created_at": _now_iso(),
            }
        ],
    )
    if not created:
        raise RuntimeError("Failed to store Telegram message map.")
    return created[0]


def find_conversation_by_telegram_message(*, telegram_chat_id: str, telegram_message_id: int) -> dict[str, Any] | None:
    cfg = _config()
    rows = _get_rows(
        cfg,
        cfg.telegram_map_table,
        params={
            "select": "conversation_id,app_message_id,telegram_chat_id,telegram_message_id,telegram_thread_id,direction",
            "telegram_chat_id": f"eq.{telegram_chat_id}",
            "telegram_message_id": f"eq.{telegram_message_id}",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


def find_latest_support_mapping_for_chat(*, telegram_chat_id: str) -> dict[str, Any] | None:
    cfg = _config()
    rows = _get_rows(
        cfg,
        cfg.telegram_map_table,
        params={
            "select": "conversation_id,app_message_id,telegram_chat_id,telegram_message_id,telegram_thread_id,direction,created_at",
            "telegram_chat_id": f"eq.{telegram_chat_id}",
            "direction": "eq.to_support",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


def get_conversation_for_user(user: dict[str, Any]) -> dict[str, Any] | None:
    cfg = _config()
    customer_user_id = _clean_text(user.get("id"))
    if not customer_user_id:
        return None
    rows = _get_rows(
        cfg,
        cfg.conversations_table,
        params={
            "select": "*",
            "customer_user_id": f"eq.{customer_user_id}",
            "order": "last_message_at.desc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None
