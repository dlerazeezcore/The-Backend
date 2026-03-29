from __future__ import annotations

import html
import mimetypes
from typing import Any

import requests

from . import supabase_repo
from .settings import read_float, read_int, read_text


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _telegram_bot_token() -> str:
    return _clean_text(read_text("telegram_bot_token"))


def _telegram_support_chat_id() -> str:
    return _clean_text(read_text("telegram_support_chat_id"))


def _telegram_support_thread_id() -> int | None:
    return read_int("telegram_support_message_thread_id")


def _telegram_secret_token() -> str:
    return _clean_text(read_text("telegram_webhook_secret"))


def _telegram_timeout_seconds() -> float:
    return read_float("telegram_timeout_seconds", 20.0)


def _telegram_api_url(method: str) -> str:
    token = _telegram_bot_token()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")
    return f"https://api.telegram.org/bot{token}/{method}"


def _telegram_display_name(user: dict[str, Any]) -> str:
    name = (
        f"{_clean_text(user.get('first_name'))} {_clean_text(user.get('last_name'))}".strip()
        or _clean_text(user.get("company_name"))
        or _clean_text(user.get("username"))
        or _clean_text(user.get("email"))
        or _clean_text(user.get("phone"))
        or "Customer"
    )
    return name


def _telegram_safe(text: str) -> str:
    return html.escape(str(text or "").strip(), quote=False)


def _preview(text: str, *, limit: int = 120) -> str:
    value = " ".join(str(text or "").split()).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _post_telegram(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        _telegram_api_url(method),
        json=payload,
        timeout=_telegram_timeout_seconds(),
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Telegram API failed ({response.status_code}): {response.text[:300]}")
    body = response.json()
    if not bool(body.get("ok")):
        raise RuntimeError(f"Telegram API returned error: {body}")
    result = body.get("result")
    return result if isinstance(result, dict) else {}


def _telegram_file_download_url(file_path: str) -> str:
    token = _telegram_bot_token()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")
    return f"https://api.telegram.org/file/bot{token}/{file_path.lstrip('/')}"


def validate_telegram_webhook_secret(header_value: str | None) -> bool:
    expected = _telegram_secret_token()
    if not expected:
        return True
    return _clean_text(header_value) == expected


def _support_message_markup(*, customer_label: str, customer_phone: str, body: str) -> str:
    lines = [
        "<b>New in-app support message</b>",
        f"<b>Customer:</b> {_telegram_safe(customer_label)}",
    ]
    if _clean_text(customer_phone):
        lines.append(f"<b>Phone:</b> <code>{_telegram_safe(customer_phone)}</code>")
    lines.extend(["", _telegram_safe(body)])
    return "\n".join(lines)


def _attachment_metadata(attachment: dict[str, Any] | None) -> dict[str, Any]:
    row = attachment if isinstance(attachment, dict) else {}
    if not row:
        return {}
    return {
        "type": "image",
        "url": _clean_text(row.get("url")),
        "bucket": _clean_text(row.get("bucket")),
        "path": _clean_text(row.get("path")),
        "name": _clean_text(row.get("name")),
        "mimeType": _clean_text(row.get("mimeType") or row.get("content_type")),
        "content_type": _clean_text(row.get("content_type")),
        "size": int(row.get("size") or 0),
    }


def _support_photo_caption(*, customer_label: str, customer_phone: str, body: str, attachment_name: str) -> str:
    lines = [
        "<b>New in-app support image</b>",
        f"<b>Customer:</b> {_telegram_safe(customer_label)}",
    ]
    if _clean_text(customer_phone):
        lines.append(f"<b>Phone:</b> <code>{_telegram_safe(customer_phone)}</code>")
    if _clean_text(attachment_name):
        lines.append(f"<b>Attachment:</b> {_telegram_safe(attachment_name)}")
    if _clean_text(body):
        lines.extend(["", _telegram_safe(body)])
    return "\n".join(lines)


def send_customer_message(user: dict[str, Any], body: str, *, attachment: dict[str, Any] | None = None) -> dict[str, Any]:
    text = _clean_text(body)
    attachment_meta = _attachment_metadata(attachment)
    if not text and not attachment_meta:
        raise RuntimeError("Message body or image attachment is required.")

    conversation = supabase_repo.get_or_create_open_conversation(user)
    metadata: dict[str, Any] = {"source": "app"}
    if attachment_meta:
        metadata["attachment"] = attachment_meta
    message = supabase_repo.create_message(
        conversation_id=str(conversation.get("id") or ""),
        sender_type="customer",
        sender_user_id=_clean_text(user.get("id")),
        sender_display_name=_telegram_display_name(user),
        body=text,
        metadata=metadata,
    )

    support_chat_id = _telegram_support_chat_id()
    if not support_chat_id:
        raise RuntimeError("TELEGRAM_SUPPORT_CHAT_ID is missing.")

    thread_id = _telegram_support_thread_id()
    telegram_method = "sendMessage"
    telegram_payload: dict[str, Any]
    if attachment_meta:
        telegram_method = "sendPhoto"
        telegram_payload = {
            "chat_id": support_chat_id,
            "photo": attachment_meta["url"],
            "caption": _support_photo_caption(
                customer_label=_telegram_display_name(user),
                customer_phone=_clean_text(user.get("phone")),
                body=text,
                attachment_name=str(attachment_meta.get("name") or ""),
            ),
            "parse_mode": "HTML",
        }
    else:
        telegram_payload = {
            "chat_id": support_chat_id,
            "text": _support_message_markup(
                customer_label=_telegram_display_name(user),
                customer_phone=_clean_text(user.get("phone")),
                body=text,
            ),
            "parse_mode": "HTML",
        }
    if thread_id is not None:
        telegram_payload["message_thread_id"] = thread_id

    telegram_result = _post_telegram(telegram_method, telegram_payload)
    telegram_message_id = telegram_result.get("message_id")
    telegram_chat = telegram_result.get("chat") if isinstance(telegram_result.get("chat"), dict) else {}
    telegram_chat_id = _clean_text(telegram_chat.get("id")) or support_chat_id

    if not isinstance(telegram_message_id, int):
        raise RuntimeError("Telegram did not return a valid message_id.")

    supabase_repo.store_telegram_map(
        conversation_id=str(conversation.get("id") or ""),
        app_message_id=str(message.get("id") or ""),
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        telegram_thread_id=thread_id,
        direction="to_support",
    )
    conversation = supabase_repo.touch_conversation(
        str(conversation.get("id") or ""),
        latest_customer_message_preview=_preview(text or f"[image] {attachment_meta.get('name') or 'attachment'}"),
        telegram_chat_id=telegram_chat_id,
        telegram_thread_id=thread_id,
    )
    return {"conversation": conversation, "message": message, "telegram_result": telegram_result}


def load_current_conversation(user: dict[str, Any]) -> dict[str, Any]:
    conversation = supabase_repo.get_conversation_for_user(user)
    if not conversation:
        raise RuntimeError("No support conversation found for this user.")
    messages = supabase_repo.list_messages(str(conversation.get("id") or ""))
    return {"conversation": conversation, "messages": messages}


def _telegram_message_text(message: dict[str, Any]) -> str:
    for key in ("text", "caption"):
        value = _clean_text(message.get(key))
        if value:
            return value
    return ""


def _sender_name(message: dict[str, Any]) -> str:
    sender = message.get("from") if isinstance(message.get("from"), dict) else {}
    full_name = f"{_clean_text(sender.get('first_name'))} {_clean_text(sender.get('last_name'))}".strip()
    if full_name:
        return full_name
    return _clean_text(sender.get("username")) or "Support"


def _extract_telegram_photo_attachment(message: dict[str, Any], conversation_id: str) -> dict[str, Any] | None:
    photo_sizes = message.get("photo") if isinstance(message.get("photo"), list) else []
    if not photo_sizes:
        return None
    candidates = [row for row in photo_sizes if isinstance(row, dict) and _clean_text(row.get("file_id"))]
    if not candidates:
        return None
    chosen = max(candidates, key=lambda row: int(row.get("file_size") or 0))
    file_id = _clean_text(chosen.get("file_id"))
    if not file_id:
        return None

    file_meta = _post_telegram("getFile", {"file_id": file_id})
    file_path = _clean_text(file_meta.get("file_path"))
    if not file_path:
        return None

    response = requests.get(_telegram_file_download_url(file_path), timeout=_telegram_timeout_seconds())
    if response.status_code >= 400:
        raise RuntimeError(f"Telegram file download failed ({response.status_code}): {response.text[:300]}")

    guessed_content_type = mimetypes.guess_type(file_path)[0] or "image/jpeg"
    filename = file_path.rsplit("/", 1)[-1] or "telegram-photo.jpg"
    return supabase_repo.upload_attachment(
        conversation_id=conversation_id,
        filename=filename,
        content=response.content,
        content_type=guessed_content_type,
    )


def handle_telegram_update(update: dict[str, Any]) -> dict[str, Any]:
    message = update.get("message") if isinstance(update.get("message"), dict) else None
    if not isinstance(message, dict):
        return {"status": "ignored", "reason": "No message payload."}

    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = _clean_text(chat.get("id"))
    if not chat_id:
        return {"status": "ignored", "reason": "Missing Telegram chat id."}
    expected_chat_id = _telegram_support_chat_id()
    if expected_chat_id and chat_id != expected_chat_id:
        return {"status": "ignored", "reason": "Message came from an unapproved Telegram chat."}

    reply_to = message.get("reply_to_message") if isinstance(message.get("reply_to_message"), dict) else None
    reply_to_message_id = reply_to.get("message_id") if isinstance(reply_to, dict) else None

    mapping = None
    if isinstance(reply_to_message_id, int):
        mapping = supabase_repo.find_conversation_by_telegram_message(
            telegram_chat_id=chat_id,
            telegram_message_id=reply_to_message_id,
        )

    if not mapping:
        mapping = supabase_repo.find_latest_support_mapping_for_chat(telegram_chat_id=chat_id)
        if not mapping:
            return {"status": "ignored", "reason": "No support conversation mapping found for Telegram reply."}

    conversation_id = _clean_text(mapping.get("conversation_id"))
    telegram_message_id = message.get("message_id")
    if not conversation_id or not isinstance(telegram_message_id, int):
        return {"status": "ignored", "reason": "Telegram reply is missing required identifiers."}
    text = _telegram_message_text(message)
    attachment = _extract_telegram_photo_attachment(message, conversation_id=conversation_id)
    attachment_meta = _attachment_metadata(attachment)
    if not text and not attachment_meta:
        return {"status": "ignored", "reason": "Only text or photo replies are supported."}

    metadata: dict[str, Any] = {"source": "telegram"}
    if attachment_meta:
        metadata["attachment"] = attachment_meta

    saved = supabase_repo.create_message(
        conversation_id=conversation_id,
        sender_type="support",
        sender_user_id=_clean_text((message.get("from") or {}).get("id")),
        sender_display_name=_sender_name(message),
        body=text,
        telegram_chat_id=chat_id,
        telegram_message_id=telegram_message_id,
        reply_to_telegram_message_id=reply_to_message_id if isinstance(reply_to_message_id, int) else None,
        metadata=metadata,
    )
    supabase_repo.store_telegram_map(
        conversation_id=conversation_id,
        app_message_id=str(saved.get("id") or ""),
        telegram_chat_id=chat_id,
        telegram_message_id=telegram_message_id,
        telegram_thread_id=message.get("message_thread_id") if isinstance(message.get("message_thread_id"), int) else None,
        direction="from_support",
    )
    supabase_repo.touch_conversation(
        conversation_id,
        latest_customer_message_preview=_preview(text or f"[image] {attachment_meta.get('name') or 'attachment'}"),
    )
    supabase_repo.touch_conversation(conversation_id)
    return {
        "status": "ok",
        "conversation_id": conversation_id,
        "message_id": str(saved.get("id") or ""),
    }
