from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
PLACEHOLDER_PREFIXES = ("PUT_", "<")
DEFAULT_ALLOWED_UPDATES = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_message",
    "edited_business_message",
)
ENV_KEY_ALIASES = {
    "telegram_bot_token": ("TELEGRAM_SUPPORT_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
    "telegram_support_chat_id": ("TELEGRAM_SUPPORT_CHAT_ID",),
    "telegram_support_message_thread_id": ("TELEGRAM_SUPPORT_MESSAGE_THREAD_ID",),
    "telegram_webhook_secret": ("TELEGRAM_SUPPORT_WEBHOOK_SECRET", "TELEGRAM_WEBHOOK_SECRET"),
    "telegram_timeout_seconds": ("TELEGRAM_SUPPORT_TIMEOUT_SECONDS", "TELEGRAM_TIMEOUT_SECONDS"),
    "telegram_public_base_url": ("TELEGRAM_SUPPORT_PUBLIC_BASE_URL", "TELEGRAM_PUBLIC_BASE_URL", "PUBLIC_BASE_URL"),
    "telegram_webhook_sync_on_startup": ("TELEGRAM_SUPPORT_WEBHOOK_SYNC_ON_STARTUP",),
    "telegram_allowed_updates": ("TELEGRAM_SUPPORT_ALLOWED_UPDATES",),
    "supabase_service_role_key": ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY"),
    "support_attachments_bucket": ("TELEGRAM_SUPPORT_ATTACHMENTS_BUCKET", "SUPPORT_ATTACHMENTS_BUCKET"),
    "support_conversations_table": ("TELEGRAM_SUPPORT_CONVERSATIONS_TABLE", "SUPPORT_CONVERSATIONS_TABLE"),
    "support_messages_table": ("TELEGRAM_SUPPORT_MESSAGES_TABLE", "SUPPORT_MESSAGES_TABLE"),
    "support_telegram_map_table": ("TELEGRAM_SUPPORT_MAP_TABLE", "SUPPORT_TELEGRAM_MAP_TABLE"),
}


@dataclass(frozen=True)
class TelegramSupportSettings:
    bot_token: str
    support_chat_id: str
    support_message_thread_id: int | None
    webhook_secret: str
    public_base_url: str
    timeout_seconds: float
    webhook_sync_on_startup: bool
    allowed_updates: tuple[str, ...]
    supabase_url: str
    supabase_service_role_key: str
    supabase_timeout_seconds: float
    attachments_bucket: str
    conversations_table: str
    messages_table: str
    telegram_map_table: str


def _load_raw() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to load Telegram config file: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Telegram config file must contain a JSON object.")
    return data


@lru_cache(maxsize=1)
def _file_settings() -> dict[str, Any]:
    return _load_raw()


def _looks_unset(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return True
        upper = stripped.upper()
        if upper in {"NONE", "NULL"}:
            return True
        if any(upper.startswith(prefix) for prefix in PLACEHOLDER_PREFIXES):
            return True
    return False


def _unwrap_text(value: Any) -> str:
    text = str(value or "").strip()
    while len(text) >= 2:
        if (text[0], text[-1]) in {('"', '"'), ("'", "'"), ("<", ">")}:
            text = text[1:-1].strip()
            continue
        break
    return text


def _env_candidates(key: str) -> tuple[str, ...]:
    explicit = ENV_KEY_ALIASES.get(key, ())
    derived = (key.upper(),)
    return explicit + tuple(name for name in derived if name not in explicit)


def read_setting(key: str, default: Any = None) -> Any:
    for env_key in _env_candidates(key):
        env_value = os.getenv(env_key)
        if not _looks_unset(env_value):
            return env_value
    value = _file_settings().get(key, None)
    if not _looks_unset(value):
        return value
    return default if value is None else value


def read_text(key: str, default: str = "") -> str:
    return _unwrap_text(read_setting(key, default) or "")


def read_int(key: str) -> int | None:
    value = read_setting(key, None)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def read_float(key: str, default: float) -> float:
    value = read_setting(key, default)
    try:
        return float(value)
    except Exception:
        return float(default)


def read_bool(key: str, default: bool) -> bool:
    value = read_setting(key, default)
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _read_csv(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = read_setting(key, None)
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        items = [str(item or "").strip() for item in value]
    else:
        items = [item.strip() for item in str(value or "").split(",")]
    cleaned = tuple(item for item in items if item)
    return cleaned or default


@lru_cache(maxsize=1)
def get_settings() -> TelegramSupportSettings:
    return TelegramSupportSettings(
        bot_token=read_text("telegram_bot_token"),
        support_chat_id=read_text("telegram_support_chat_id"),
        support_message_thread_id=read_int("telegram_support_message_thread_id"),
        webhook_secret=read_text("telegram_webhook_secret"),
        public_base_url=read_text("telegram_public_base_url").rstrip("/"),
        timeout_seconds=read_float("telegram_timeout_seconds", 20.0),
        webhook_sync_on_startup=read_bool("telegram_webhook_sync_on_startup", True),
        allowed_updates=_read_csv("telegram_allowed_updates", DEFAULT_ALLOWED_UPDATES),
        supabase_url=read_text("supabase_url").rstrip("/"),
        supabase_service_role_key=read_text("supabase_service_role_key"),
        supabase_timeout_seconds=read_float("supabase_timeout_seconds", 20.0),
        attachments_bucket=read_text("support_attachments_bucket", "support-attachments") or "support-attachments",
        conversations_table=read_text("support_conversations_table", "support_conversations") or "support_conversations",
        messages_table=read_text("support_messages_table", "support_messages") or "support_messages",
        telegram_map_table=read_text("support_telegram_map_table", "support_telegram_map") or "support_telegram_map",
    )


def clear_settings_cache() -> None:
    _file_settings.cache_clear()
    get_settings.cache_clear()
