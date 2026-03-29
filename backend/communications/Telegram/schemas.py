from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CustomerMessageRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


class SupportMessageRecord(BaseModel):
    id: str
    conversation_id: str
    sender_type: str
    sender_user_id: str = ""
    sender_display_name: str = ""
    body: str
    telegram_chat_id: str | None = None
    telegram_message_id: int | None = None
    reply_to_telegram_message_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class SupportConversationRecord(BaseModel):
    id: str
    customer_user_id: str
    customer_display_name: str = ""
    status: str = "open"
    source: str = "in_app"
    telegram_chat_id: str | None = None
    telegram_thread_id: int | None = None
    latest_customer_message_preview: str = ""
    created_at: str
    updated_at: str
    last_message_at: str


class ConversationResponse(BaseModel):
    conversation: SupportConversationRecord
    messages: list[SupportMessageRecord] = Field(default_factory=list)
